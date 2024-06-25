# pylint: disable=too-many-arguments
import base64
import logging
import os
import traceback
from datetime import datetime
from decimal import Decimal
from os.path import basename
from typing import Optional, Sequence, List, Dict
from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.admin import SimpleListFilter
from django.contrib.auth.models import User  # noqa
from django.contrib.messages import add_message, ERROR
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import HttpRequest, Http404
from django.shortcuts import render, get_object_or_404
from django.urls import ResolverMatch, reverse, path, re_path, URLPattern
from django.utils.formats import date_format, localize
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.text import capfirst
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from jacc.admin import AccountEntryNoteInline, AccountEntryNoteAdmin
from jacc.helpers import sum_queryset
from jacc.models import Account, EntryType, AccountEntryNote

from jbank.helpers import limit_filename_length
from jbank.services import filter_settlements_for_bank_reconciliation
from jutil.format import dec2, format_timedelta, choices_label
from jutil.request import get_ip
from jutil.responses import FormattedXmlResponse, FormattedXmlFileResponse
from jutil.validators import iban_bic
from jutil.xml import xml_to_dict
from jbank.models import (
    Statement,
    StatementRecord,
    StatementRecordSepaInfo,
    ReferencePaymentRecord,
    ReferencePaymentBatch,
    StatementFile,
    ReferencePaymentBatchFile,
    Payout,
    Refund,
    PayoutStatus,
    PayoutParty,
    StatementRecordDetail,
    StatementRecordRemittanceInfo,
    CurrencyExchange,
    CurrencyExchangeSource,
    WsEdiConnection,
    WsEdiSoapCall,
    EuriborRate,
    PAYOUT_WAITING_PROCESSING,
    PAYOUT_ERROR,
    PAYOUT_PAID,
    PAYOUT_ON_HOLD,
    AccountBalance,
    PAYOUT_STATE,
)
from jbank.tito import parse_tiliote_statements_from_file, parse_tiliote_statements
from jbank.svm import parse_svm_batches_from_file, parse_svm_batches, create_statement, create_reference_payment_batch
from jutil.admin import ModelAdminBase, admin_log

logger = logging.getLogger(__name__)


class BankAdminBase(ModelAdminBase):
    save_on_top = False

    def save_formset(self, request, form, formset, change):
        if formset.model == AccountEntryNote:
            AccountEntryNoteAdmin.save_account_entry_note_formset(request, form, formset, change)
        else:
            formset.save()

    @staticmethod
    def format_admin_obj_link_list(qs: QuerySet, route: str):
        out = ""
        for e_id in list(qs.order_by("id").values_list("id", flat=True)):
            if out:
                out += " | "
            url_path = reverse(route, args=[e_id])
            out += f'<a href="{url_path}">id={e_id}</a>'
        return mark_safe(out)

    @staticmethod
    def format_currency(value: Optional[Decimal]) -> str:
        if value is None:
            return ""
        value_str = localize(dec2(value))
        out = value_str[-3] + value_str[-2:]
        value_str = value_str[:-3]
        while len(value_str) > 3:
            out = "&nbsp;" + value_str[-3:] + out
            value_str = value_str[:-3]
        out = value_str + out
        return mark_safe(out)


class SettlementEntryTypesFilter(SimpleListFilter):
    """Filters incoming settlement type entries."""

    title = _("account entry types")
    parameter_name = "type"

    def lookups(self, request, model_admin):
        choices = []
        for e in EntryType.objects.all().filter(is_settlement=True).order_by("name"):
            assert isinstance(e, EntryType)
            choices.append((e.id, capfirst(e.name)))
        return choices

    def queryset(self, request, queryset):
        val = self.value()
        if val:
            return queryset.filter(type__id=val)
        return queryset


class AccountEntryMatchedFilter(SimpleListFilter):
    """Filters incoming payments which do not have any child/derived account entries."""

    title = _("account.entry.matched.filter")
    parameter_name = "matched"

    def lookups(self, request, model_admin):
        return [
            ("1", capfirst(_("account.entry.not.matched"))),
            ("2", capfirst(_("account.entry.is.matched"))),
            ("4", capfirst(_("not marked as settled"))),
            ("3", capfirst(_("marked as settled"))),
        ]

    def queryset(self, request, queryset):
        val = self.value()
        if val:
            # return original settlements only
            queryset = queryset.filter(type__is_settlement=True, parent=None)
            if val == "1":
                return filter_settlements_for_bank_reconciliation(queryset)
            if val == "2":
                # return any entries with derived account entries or marked as reconciled
                return queryset.exclude(Q(child_set=None) & Q(marked_reconciled=False))
            if val == "3":
                # return only manually marked as settled
                return queryset.filter(marked_reconciled=True)
            if val == "4":
                # return everything but manually marked as settled
                return queryset.filter(marked_reconciled=False)
        return queryset


class AccountNameFilter(SimpleListFilter):
    """Filters account entries based on account name."""

    title = _("account.name.filter")
    parameter_name = "account-name"

    def lookups(self, request, model_admin):
        ops = []
        qs = model_admin.get_queryset(request)
        for e in qs.distinct("account__name"):
            ops.append((e.account.name, e.account.name))
        return sorted(ops, key=lambda x: x[0])

    def queryset(self, request, queryset):
        val = self.value()
        if val:
            return queryset.filter(account__name=val)
        return queryset


class StatementAdmin(BankAdminBase):
    exclude = ()
    list_per_page = 20
    save_on_top = False
    ordering = ("-record_date", "account_number")
    date_hierarchy = "end_date"
    list_filter = ("account_number",)
    readonly_fields = (
        "file_link",
        "account_number",
        "bic_code",
        "statement_number",
        "begin_date",
        "end_date",
        "record_date",
        "customer_identifier",
        "begin_balance_date",
        "begin_balance",
        "record_count",
        "currency_code",
        "account_name",
        "account_limit",
        "owner_name",
        "contact_info_1",
        "contact_info_2",
        "bank_specific_info_1",
        "iban",
        "bic",
    )
    fields = readonly_fields
    search_fields = (
        "name",
        "statement_number",
    )
    list_display = (
        "id",
        "statement_date_short",
        "account_number",
        "bic_code",
        "statement_number",
        "begin_balance",
        "currency_code",
        "file_link",
        "account_entry_list",
    )

    def bic_code(self, obj):
        assert isinstance(obj, Statement)
        return iban_bic(obj.account_number)

    bic_code.short_description = "BIC"  # type: ignore

    def statement_date_short(self, obj):
        assert isinstance(obj, Statement)
        return date_format(obj.end_date, "SHORT_DATE_FORMAT")

    statement_date_short.short_description = _("date")  # type: ignore
    statement_date_short.admin_order_field = "end_date"  # type: ignore

    def record_date_short(self, obj):
        assert isinstance(obj, Statement)
        return date_format(obj.record_date, "SHORT_DATE_FORMAT")

    record_date_short.short_description = _("record date")  # type: ignore
    record_date_short.admin_order_field = "record_date"  # type: ignore

    def account_entry_list(self, obj):
        assert isinstance(obj, Statement)
        admin_url = reverse("admin:jbank_statementrecord_statement_changelist", args=(obj.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), StatementRecord.objects.filter(statement=obj).count())

    account_entry_list.short_description = _("account entries")  # type: ignore

    def file_link(self, obj):
        assert isinstance(obj, Statement)
        if not obj.file:
            return ""
        admin_url = reverse("admin:jbank_statementfile_change", args=(obj.file.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.name)

    file_link.admin_order_field = "file"  # type: ignore
    file_link.short_description = _("file")  # type: ignore

    def get_urls(self):
        return [
            path(
                "by-file/<int:file_id>/",
                self.admin_site.admin_view(self.kw_changelist_view),
                name="jbank_statement_file_changelist",
            ),
        ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        file_id = rm.kwargs.get("file_id", None)
        if file_id:
            qs = qs.filter(file=file_id)
        return qs


class StatementRecordDetailInlineAdmin(admin.StackedInline):
    exclude = ()
    model = StatementRecordDetail
    can_delete = False
    extra = 0

    fields = (
        "batch_identifier",
        "amount",
        "creditor_account",
        "creditor_account_scheme",
        "currency_code",
        "instructed_amount",
        "exchange",
        "archive_identifier",
        "end_to_end_identifier",
        "creditor_name",
        "debtor_name",
        "ultimate_debtor_name",
        "unstructured_remittance_info",
        "paid_date",
        "structured_remittance_info",
    )
    readonly_fields = fields
    raw_id_fields = ()

    def structured_remittance_info(self, obj):
        assert isinstance(obj, StatementRecordDetail)
        lines = []
        for rinfo in obj.remittanceinfo_set.all().order_by("id"):
            assert isinstance(rinfo, StatementRecordRemittanceInfo)
            lines.append(str(rinfo))
        return mark_safe("<br>".join(lines))

    structured_remittance_info.short_description = _("structured remittance info")  # type: ignore

    def has_add_permission(self, request, obj=None):
        return False


class StatementRecordSepaInfoInlineAdmin(admin.StackedInline):
    exclude = ()
    model = StatementRecordSepaInfo
    can_delete = False
    extra = 0
    max_num = 1

    readonly_fields = (
        "record",
        "reference",
        "iban_account_number",
        "bic_code",
        "recipient_name_detail",
        "payer_name_detail",
        "identifier",
        "archive_identifier",
    )
    raw_id_fields = ("record",)

    def has_add_permission(self, request, obj=None):
        return False


def mark_as_marked_reconciled(modeladmin, request, qs):  # pylint: disable=unused-argument
    try:
        data = request.POST.dict()

        if "description" in data:
            description = data["description"]
            user = request.user
            assert isinstance(user, User)
            for e in list(qs.filter(marked_reconciled=False)):
                e.marked_reconciled = True
                e.save(update_fields=["marked_reconciled"])
                msg = "{}: {}".format(capfirst(_("marked as reconciled")), description)
                admin_log([e], msg, who=user)
                messages.info(request, "{}: {}".format(e, msg))
        else:
            cx = {
                "qs": qs,
            }
            return render(request, "admin/jbank/mark_as_marked_reconciled.html", context=cx)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    except Exception as e:
        logger.error("mark_as_marked_reconciled: %s", traceback.format_exc())
        messages.error(request, "{}".format(e))
    return None


def unmark_marked_reconciled_flag(modeladmin, request, qs):  # pylint: disable=unused-argument
    user = request.user
    for e in list(qs.filter(marked_reconciled=True)):
        e.marked_reconciled = False
        e.save(update_fields=["marked_reconciled"])
        msg = capfirst(_("reconciled mark removed"))
        admin_log([e], msg, who=user)
        messages.info(request, "{}: {}".format(e, msg))


def summarize_records(modeladmin, request, qs):  # pylint: disable=unused-argument
    total = Decimal("0.00")
    out = "<table><tr><td style='text-align: left'>" + _("type") + "</td>" + "<td style='text-align: right'>" + _("amount") + "</td></tr>"
    for e_type in qs.order_by("type").distinct("type"):
        amt = sum_queryset(qs.filter(type=e_type.type))
        type_name = e_type.type.name if e_type.type else ""
        out += "<tr>"
        out += "<td style='text-align: left'>" + type_name + "</td>"
        out += "<td style='text-align: right'>" + localize(amt) + "</td>"
        out += "</tr>"
        total += amt
    out += "<tr>"
    out += "<td style='text-align: left'>" + _("total") + "</td>"
    out += "<td style='text-align: right'>" + localize(total) + "</td>"
    out += "</tr>"
    out += "</table>"
    messages.info(request, mark_safe(out))


class StatementRecordAdmin(BankAdminBase):
    list_per_page = 25
    save_on_top = False
    date_hierarchy = "value_date"
    fields = (
        "id",
        "entry_type",
        "statement",
        "line_number",
        "file_link",
        "record_number",
        "archive_identifier",
        "record_date",
        "value_date",
        "paid_date",
        "type",
        "record_code",
        "record_domain",
        "family_code",
        "sub_family_code",
        "record_description",
        "amount",
        "receipt_code",
        "delivery_method",
        "name",
        "name_source",
        "recipient_account_number",
        "recipient_account_number_changed",
        "remittance_info",
        "messages",
        "client_messages",
        "bank_messages",
        "account",
        "timestamp",
        "created",
        "last_modified",
        "description",
        "source_file",
        "archived",
        "marked_reconciled",
        "is_reconciled_bool",
        "child_links",
    )
    readonly_fields = fields
    raw_id_fields = (
        "statement",
        # from AccountEntry
        "account",
        "source_file",
        "parent",
        "source_invoice",
        "settled_invoice",
        "settled_item",
    )
    list_filter = (
        "statement__file__tag",
        AccountNameFilter,
        "marked_reconciled",
        SettlementEntryTypesFilter,
        "record_code",
    )
    search_fields = (
        "=archive_identifier",
        "=amount",
        "=recipient_account_number",
        "record_description",
        "name",
        "remittance_info",
        "messages",
    )
    list_display = (
        "id",
        "value_date_short",
        "type",
        "amount",
        "name",
        "source_file_link",
        "is_reconciled_bool",
    )
    inlines = (
        StatementRecordSepaInfoInlineAdmin,
        StatementRecordDetailInlineAdmin,
        AccountEntryNoteInline,
    )
    actions = (
        mark_as_marked_reconciled,
        unmark_marked_reconciled_flag,
        summarize_records,
    )

    def is_reconciled_bool(self, obj):
        return obj.is_reconciled

    is_reconciled_bool.short_description = _("reconciled")  # type: ignore
    is_reconciled_bool.boolean = True  # type: ignore

    def value_date_short(self, obj):
        return date_format(obj.value_date, "SHORT_DATE_FORMAT")

    value_date_short.short_description = _("date.short")  # type: ignore
    value_date_short.admin_order_field = "value_date"  # type: ignore

    def record_date_short(self, obj):
        return date_format(obj.record_date, "SHORT_DATE_FORMAT")

    record_date_short.short_description = _("record date")  # type: ignore
    record_date_short.admin_order_field = "record_date"  # type: ignore

    def child_links(self, obj) -> str:
        assert isinstance(obj, StatementRecord)
        return self.format_admin_obj_link_list(obj.child_set, "admin:jacc_accountentry_change")  # type: ignore

    child_links.short_description = _("derived entries")  # type: ignore

    def get_urls(self):
        return [
            path(
                "by-statement/<int:statement_id>/",
                self.admin_site.admin_view(self.kw_changelist_view),
                name="jbank_statementrecord_statement_changelist",
            ),
            path(
                "by-statement-file/<int:statement_file_id>/",
                self.admin_site.admin_view(self.kw_changelist_view),
                name="jbank_statementrecord_statementfile_changelist",
            ),
        ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        statement_id = rm.kwargs.get("statement_id", None)
        if statement_id:
            qs = qs.filter(statement__id=statement_id)
        statement_file_id = rm.kwargs.get("statement_file_id", None)
        if statement_file_id:
            qs = qs.filter(statement__file_id=statement_file_id)
        return qs

    @admin.display(description=_("source file"), ordering="statement")  # type: ignore
    def source_file_link(self, obj):
        assert isinstance(obj, StatementRecord)
        stm = obj.statement
        if not stm:
            return ""
        admin_url = reverse("admin:jbank_statementfile_change", args=(stm.file.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), mark_safe(limit_filename_length(stm.name, 12, "&hellip;")))

    def file_link(self, obj):
        assert isinstance(obj, StatementRecord)
        if not obj.statement or not obj.statement.file:
            return ""
        name = basename(obj.statement.file.file.name)
        admin_url = reverse("admin:jbank_statementfile_change", args=(obj.statement.file.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), name)

    file_link.admin_order_field = "file"  # type: ignore
    file_link.short_description = _("account statement file")  # type: ignore


class ReferencePaymentRecordAdmin(BankAdminBase):
    exclude = ()
    list_per_page = 25
    save_on_top = False
    date_hierarchy = "record_date"
    raw_id_fields = (
        "batch",
        # from AccountEntry
        "account",
        "source_file",
        "parent",
        "source_invoice",
        "settled_invoice",
        "settled_item",
    )
    fields = [
        "id",
        "batch",
        "line_number",
        "file_link",
        "record_type",
        "account_number",
        "record_date",
        "paid_date",
        "value_date",
        "archive_identifier",
        "remittance_info",
        "payer_name",
        "currency_identifier",
        "name_source",
        "amount",
        "correction_identifier",
        "delivery_method",
        "receipt_code",
        "archived",
        "account",
        "created",
        "last_modified",
        "timestamp",
        "type",
        "description",
        "marked_reconciled",
        "is_reconciled_bool",
        "child_links",
        "instructed_amount",
        "instructed_currency",
        "creditor_bank_bic",
        "end_to_end_identifier",
    ]
    readonly_fields = (
        "id",
        "batch",
        "line_number",
        "file_link",
        "record_type",
        "account_number",
        "record_date",
        "paid_date",
        "value_date",
        "archive_identifier",
        "remittance_info",
        "payer_name",
        "currency_identifier",
        "name_source",
        "amount",
        "correction_identifier",
        "delivery_method",
        "receipt_code",
        "archived",
        "marked_reconciled",
        "account",
        "timestamp",
        "created",
        "last_modified",
        "type",
        "description",
        "amount",
        "source_file",
        "source_invoice",
        "settled_invoice",
        "settled_item",
        "parent",
        "is_reconciled_bool",
        "child_links",
        "instructed_amount",
        "instructed_currency",
        "creditor_bank_bic",
        "end_to_end_identifier",
    )
    list_filter = (
        "batch__file__tag",
        AccountNameFilter,
        AccountEntryMatchedFilter,
        "correction_identifier",
    )
    search_fields = (
        "=archive_identifier",
        "=amount",
        "remittance_info",
        "payer_name",
        "batch__name",
    )
    list_display = (
        "id",
        "record_date",
        "type",
        "amount",
        "payer_name",
        "remittance_info",
        "source_file_link",
        "is_reconciled_bool",
    )
    actions = (
        mark_as_marked_reconciled,
        unmark_marked_reconciled_flag,
        summarize_records,
    )
    inlines = [
        AccountEntryNoteInline,
    ]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def is_reconciled_bool(self, obj):
        return obj.is_reconciled

    is_reconciled_bool.short_description = _("reconciled")  # type: ignore
    is_reconciled_bool.boolean = True  # type: ignore

    def record_date_short(self, obj):
        return date_format(obj.record_date, "SHORT_DATE_FORMAT")

    record_date_short.short_description = _("date.short")  # type: ignore
    record_date_short.admin_order_field = "record_date"  # type: ignore

    def child_links(self, obj) -> str:
        assert isinstance(obj, ReferencePaymentRecord)
        return self.format_admin_obj_link_list(obj.child_set, "admin:jacc_accountentry_change")  # type: ignore

    child_links.short_description = _("derived entries")  # type: ignore

    def file_link(self, obj):
        assert isinstance(obj, ReferencePaymentRecord)
        if not obj.batch or not obj.batch.file:
            return ""
        admin_url = reverse("admin:jbank_referencepaymentbatchfile_change", args=(obj.batch.file.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.batch.file)

    file_link.admin_order_field = "file"  # type: ignore
    file_link.short_description = _("file")  # type: ignore

    def get_urls(self):
        return [
            path(
                "by-batch/<int:batch_id>/",
                self.admin_site.admin_view(self.kw_changelist_view),
                name="jbank_referencepaymentrecord_batch_changelist",
            ),
            path(
                "by-statement-file/<int:stm_id>/",
                self.admin_site.admin_view(self.kw_changelist_view),
                name="jbank_referencepaymentrecord_statementfile_changelist",
            ),
        ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        batch_id = rm.kwargs.get("batch_id", None)
        if batch_id:
            qs = qs.filter(batch_id=batch_id)
        stm_id = rm.kwargs.get("stm_id", None)
        if stm_id:
            qs = qs.filter(batch__file_id=stm_id)
        return qs

    @admin.display(description=_("source file"), ordering="batch")  # type: ignore
    def source_file_link(self, obj):
        assert isinstance(obj, ReferencePaymentRecord)
        if not obj.batch:
            return ""
        admin_url = reverse("admin:jbank_referencepaymentbatchfile_change", args=(obj.batch.file.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), mark_safe(limit_filename_length(obj.batch.name, 12, "&hellip;")))


class ReferencePaymentBatchAdmin(BankAdminBase):
    exclude = ()
    list_per_page = 20
    save_on_top = False
    ordering = ("-record_date",)
    date_hierarchy = "record_date"
    list_filter = ("record_set__account_number",)
    fields = (
        "file_link",
        "record_date",
        "institution_identifier",
        "service_identifier",
        "currency_identifier",
    )
    readonly_fields = (
        "name",
        "file",
        "file_link",
        "record_date",
        "institution_identifier",
        "service_identifier",
        "currency_identifier",
    )
    search_fields = (
        "name",
        "=record_set__archive_identifier",
        "=record_set__amount",
        "record_set__remittance_info",
        "record_set__payer_name",
    )
    list_display = (
        "id",
        "record_date_short",
        "name",
        "service_identifier",
        "currency_identifier",
        "account_entry_list",
    )

    def record_date_short(self, obj):
        return date_format(obj.record_date, "SHORT_DATE_FORMAT")

    record_date_short.short_description = _("record date")  # type: ignore
    record_date_short.admin_order_field = "record_date"  # type: ignore

    def account_entry_list(self, obj):
        assert isinstance(obj, ReferencePaymentBatch)
        admin_url = reverse("admin:jbank_referencepaymentrecord_batch_changelist", args=(obj.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), ReferencePaymentRecord.objects.filter(batch=obj).count())

    account_entry_list.short_description = _("account entries")  # type: ignore

    def file_link(self, obj):
        assert isinstance(obj, ReferencePaymentBatch)
        if not obj.file:
            return ""
        admin_url = reverse("admin:jbank_referencepaymentbatchfile_change", args=(obj.file.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.file)

    file_link.admin_order_field = "file"  # type: ignore
    file_link.short_description = _("file")  # type: ignore

    def get_urls(self):
        return [
            path(
                "by-file/<int:file_id>/",
                self.admin_site.admin_view(self.kw_changelist_view),
                name="jbank_referencepaymentbatch_file_changelist",
            ),
        ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        file_id = rm.kwargs.get("file_id", None)
        if file_id:
            qs = qs.filter(file=file_id)
        return qs


class StatementFileForm(forms.ModelForm):
    class Meta:
        fields = [
            "file",
        ]

    def clean_file(self):
        file = self.cleaned_data["file"]
        assert isinstance(file, InMemoryUploadedFile)
        name = file.name
        file.seek(0)
        content = file.read()
        assert isinstance(content, bytes)
        try:
            statements = parse_tiliote_statements(content.decode("ISO-8859-1"), filename=basename(name))
            for stm in statements:
                account_number = stm["header"]["account_number"]
                if Account.objects.filter(name=account_number).count() == 0:
                    raise ValidationError(_("account.not.found").format(account_number=account_number))
        except ValidationError:
            raise
        except Exception as err:
            raise ValidationError(_("Unhandled error") + ": {}".format(err)) from err
        return file


class StatementFileAdmin(BankAdminBase):
    save_on_top = False
    exclude = ()
    form = StatementFileForm

    date_hierarchy = "created"

    search_fields = ("original_filename__contains",)

    list_filter = ("tag",)

    list_display = (
        "id",
        "created",
        "file",
        "records",
    )

    readonly_fields = (
        "created",
        "errors",
        "file",
        "original_filename",
        "records",
    )

    def records(self, obj):
        assert isinstance(obj, StatementFile)
        admin_url = reverse("admin:jbank_statementrecord_statementfile_changelist", args=[obj.id])
        return format_html('<a href="{}">{}</a>', admin_url, _("statement records"))

    records.short_description = _("statement records")  # type: ignore

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def construct_change_message(self, request, form, formsets, add=False):
        if add:
            instance = form.instance
            assert isinstance(instance, StatementFile)
            if instance.file:
                full_path = instance.full_path
                plain_filename = basename(full_path)
                try:
                    statements = parse_tiliote_statements_from_file(full_path)
                    with transaction.atomic():
                        for data in statements:
                            create_statement(data, name=plain_filename, file=instance)
                except Exception as e:
                    instance.errors = traceback.format_exc()
                    instance.save()
                    add_message(request, ERROR, str(e))
                    instance.delete()

        return super().construct_change_message(request, form, formsets, add)


class ReferencePaymentBatchFileForm(forms.ModelForm):
    class Meta:
        fields = [
            "file",
        ]

    def clean_file(self):
        file = self.cleaned_data["file"]
        assert isinstance(file, InMemoryUploadedFile)
        name = file.name
        file.seek(0)
        content = file.read()
        assert isinstance(content, bytes)
        try:
            batches = parse_svm_batches(content.decode("ISO-8859-1"), filename=basename(name))
            for b in batches:
                for rec in b["records"]:
                    account_number = rec["account_number"]
                    if Account.objects.filter(name=account_number).count() == 0:
                        raise ValidationError(_("account.not.found").format(account_number=account_number))
        except ValidationError:
            raise
        except Exception as err:
            raise ValidationError(_("Unhandled error") + ": {}".format(err)) from err
        return file


class ReferencePaymentBatchFileAdmin(BankAdminBase):
    save_on_top = False
    form = ReferencePaymentBatchFileForm
    date_hierarchy = "created"

    fields = [
        "created",
        "file",
        "original_filename",
        "tag",
        "timestamp",
        "msg_id",
        "additional_info",
        "errors",
        "cached_total_amount",
    ]

    list_display = (
        "id",
        "created",
        "file",
        "total",
    )

    list_filter = ("tag",)

    search_fields = ("file__contains",)

    readonly_fields = (
        "created",
        "file",
        "original_filename",
        "tag",
        "timestamp",
        "msg_id",
        "additional_info",
        "errors",
        "cached_total_amount",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def total(self, obj):
        assert isinstance(obj, ReferencePaymentBatchFile)
        path = reverse("admin:jbank_referencepaymentrecord_statementfile_changelist", args=[obj.id])
        return format_html('<a href="{}">{}</a>', path, localize(obj.total_amount))

    total.short_description = _("total amount")  # type: ignore

    def construct_change_message(self, request, form, formsets, add=False):
        if add:
            instance = form.instance
            assert isinstance(instance, ReferencePaymentBatchFile)
            if instance.file:
                full_path = instance.full_path
                plain_filename = basename(full_path)
                try:
                    batches = parse_svm_batches_from_file(full_path)
                    with transaction.atomic():
                        for data in batches:
                            create_reference_payment_batch(data, name=plain_filename, file=instance)
                except Exception as e:
                    user = request.user
                    assert isinstance(user, User)
                    instance.errors = traceback.format_exc()
                    instance.save()
                    msg = str(e)
                    if user.is_superuser:
                        msg = instance.errors
                    logger.error("%s: %s", plain_filename, msg)
                    add_message(request, ERROR, msg)
                    instance.delete()

        return super().construct_change_message(request, form, formsets, add)


class PayoutStatusAdminMixin:
    def created_brief(self, obj):
        assert isinstance(obj, PayoutStatus)
        return date_format(obj.created, "SHORT_DATETIME_FORMAT")

    created_brief.short_description = _("created")  # type: ignore
    created_brief.admin_order_field = "created"  # type: ignore

    def timestamp_brief(self, obj):
        assert isinstance(obj, PayoutStatus)
        return date_format(obj.timestamp, "SHORT_DATETIME_FORMAT") if obj.timestamp else ""

    timestamp_brief.short_description = _("timestamp")  # type: ignore
    timestamp_brief.admin_order_field = "timestamp"  # type: ignore

    def file_name_link(self, obj):
        assert isinstance(obj, PayoutStatus)
        if obj.id is None or not obj.full_path:
            return obj.file_name
        admin_url = reverse(
            "admin:jbank_payoutstatus_file_download",
            args=(
                obj.id,
                obj.file_name,
            ),
        )
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.file_name)

    file_name_link.short_description = _("file")  # type: ignore
    file_name_link.admin_order_field = "file_name"  # type: ignore


class PayoutStatusAdmin(BankAdminBase, PayoutStatusAdminMixin):
    fields = (
        "created_brief",
        "timestamp_brief",
        "payout",
        "file_name_link",
        "response_code",
        "response_text",
        "msg_id",
        "original_msg_id",
        "group_status",
        "status_reason",
    )
    date_hierarchy = "created"
    readonly_fields = fields
    list_filter = [
        "group_status",
    ]
    list_display = (
        "id",
        "created_brief",
        "timestamp_brief",
        "payout",
        "file_name_link",
        "response_code",
        "response_text",
        "original_msg_id",
        "group_status",
    )

    def file_download_view(self, request, pk, filename, form_url="", extra_context=None):  # pylint: disable=unused-argument
        user = request.user
        if not user.is_authenticated or not user.is_staff:
            raise Http404(_("File {} not found").format(filename))
        obj = get_object_or_404(self.get_queryset(request), pk=pk, file_name=filename)
        assert isinstance(obj, PayoutStatus)
        full_path = obj.full_path
        if not os.path.isfile(full_path):
            raise Http404(_("File {} not found").format(filename))
        return FormattedXmlFileResponse(full_path)

    def get_urls(self):
        urls = [
            re_path(
                r"^(\d+)/change/status-downloads/(.+)/$",
                self.file_download_view,
                name="jbank_payoutstatus_file_download",
            ),
        ]
        return urls + super().get_urls()


class PayoutStatusInlineAdmin(admin.TabularInline, PayoutStatusAdminMixin):
    model = PayoutStatus
    can_delete = False
    extra = 0
    ordering = ("-id",)
    fields = (
        "created_brief",
        "timestamp_brief",
        "payout",
        "file_name_link",
        "response_code",
        "response_text",
        "msg_id",
        "original_msg_id",
        "group_status",
        "status_reason",
    )
    readonly_fields = fields


def send_payouts_to_bank(modeladmin, request, qs):  # pylint: disable=unused-argument
    user_ip = get_ip(request)
    qs = qs.filter(state__in=[PAYOUT_ERROR, PAYOUT_ON_HOLD])
    n_count = qs.count()
    qs_list = list(qs.order_by("id").distinct())
    qs.update(state=PAYOUT_WAITING_PROCESSING)
    state_name = choices_label(PAYOUT_STATE, PAYOUT_WAITING_PROCESSING)
    messages.success(request, f"{n_count}x {state_name}")
    admin_log(qs_list, f"Changed state to {state_name}", who=request.user, ip=user_ip)


def mark_payouts_as_paid(modeladmin, request, qs):  # pylint: disable=unused-argument
    user_ip = get_ip(request)
    qs = qs.exclude(state__in=[PAYOUT_PAID])
    n_count = qs.count()
    qs_list = list(qs.order_by("id").distinct())
    qs.update(state=PAYOUT_PAID)
    state_name = choices_label(PAYOUT_STATE, PAYOUT_WAITING_PROCESSING)
    messages.success(request, f"{n_count}x {state_name}")
    admin_log(qs_list, f"Changed state to {state_name}", who=request.user, ip=user_ip)


def regenerate_payout_message_identifiers(modeladmin, request, qs):  # pylint: disable=unused-argument
    user_ip = get_ip(request)
    n_count = 0
    for p in list(qs.order_by("id").distinct()):
        assert isinstance(p, Payout)
        try:
            if p.state == PAYOUT_PAID:
                messages.warning(request, f"{p}: {p.state_name}")
                continue
            old = p.msg_id
            p.generate_msg_id(commit=False)
            p.file_name = ""
            p.save(update_fields=["msg_id", "file_name"])
            admin_log([p], f"Regenerated msg_id from {old} to {p.msg_id}, file name reset", who=request.user, ip=user_ip)
            n_count += 1
        except Exception as err:
            messages.error(request, f"{p}: {err}")
    messages.success(request, f"{n_count} message IDs regenerated and pain001 file names reset")


def show_payout_summary(modeladmin, request, queryset):  # pylint: disable=unused-argument
    queryset = queryset.order_by("id").distinct()
    by_group_status: Dict[str, List[Decimal, int]] = {}
    for obj in queryset:
        assert isinstance(obj, Payout)
        by_group_status.setdefault(obj.group_status, [Decimal("0.00"), 0])
        by_group_status[obj.group_status][0] += obj.amount
        by_group_status[obj.group_status][1] += 1
    out = "<table>"
    all_amt, all_count = Decimal("0.00"), 0
    for group_status in sorted(by_group_status.keys()):
        total_amt, total_count = by_group_status[group_status]
        all_count += total_count
        all_amt += total_amt
        out += (
            f"<tr><td style='text-align: right'>{total_count}</td><td>x</td>"
            f"<td>{group_status}</td><td>=</td><td style='text-align: right'>{total_amt}</td></tr>"
        )
    out += (
        f"<tr><td style='text-align: right; font-weight: bold'>{all_count}</td><td>&nbsp;</td>"
        f"<td>&nbsp;</td><td>=</td><td style='text-align: right; font-weight: bold'>{all_amt}</td></tr>"
    )
    out += "</table>"
    messages.info(request, mark_safe(out))


class PayoutAdmin(BankAdminBase):
    save_on_top = False
    inlines = [PayoutStatusInlineAdmin, AccountEntryNoteInline]
    date_hierarchy = "timestamp"
    list_max_show_all = 1000

    actions = [
        send_payouts_to_bank,
        mark_payouts_as_paid,
        regenerate_payout_message_identifiers,
        show_payout_summary,
    ]

    raw_id_fields: Sequence[str] = (
        "account",
        "parent",
        "payer",
        "recipient",
    )

    list_filter = [
        "state",
        "payoutstatus_set__response_code",
        "payoutstatus_set__group_status",
        "recipient__bic",
    ]

    fields = [
        "connection",
        "account",
        "parent",
        "payer",
        "recipient",
        "amount",
        "messages",
        "reference",
        "due_date",
        "msg_id",
        "file_name",
        "timestamp",
        "paid_date",
        "state",
        "group_status",
        "created",
    ]

    list_display = [
        "id",
        "timestamp",
        "recipient",
        "amount",
        "paid_date_brief",
        "state",
    ]

    readonly_fields = [
        "created",
        "paid_date",
        "timestamp",
        "msg_id",
        "file_name",
        "group_status",
    ]

    search_fields = [
        "=msg_id",
        "=file_name",
        "=file_reference",
        "recipient__name",
        "=recipient__account_number",
        "=msg_id",
        "=amount",
    ]

    def paid_date_brief(self, obj):
        assert isinstance(obj, Payout)
        return date_format(obj.paid_date, "SHORT_DATETIME_FORMAT") if obj.paid_date else ""

    paid_date_brief.short_description = _("paid date")  # type: ignore
    paid_date_brief.admin_order_field = "paid_date"  # type: ignore

    def save_model(self, request, obj, form, change):
        assert isinstance(obj, Payout)
        if not change:
            if not hasattr(obj, "account") or not obj.account:
                obj.account = obj.payer.payouts_account
            if not hasattr(obj, "type") or not obj.type:
                obj.type = EntryType.objects.get(code=settings.E_BANK_PAYOUT)
        return super().save_model(request, obj, form, change)


class PayoutPartyAdmin(BankAdminBase):
    save_on_top = False
    search_fields = (
        "name",
        "=account_number",
        "=org_id",
    )
    ordering = ("name",)
    fields = [
        "name",
        "account_number",
        "bic",
        "org_id",
        "address",
        "country_code",
        "payouts_account",
    ]
    readonly_fields: List[str] = []
    actions = ()

    list_display = (
        "id",
        "name",
        "account_number",
        "bic",
        "org_id",
        "address",
        "country_code",
    )

    raw_id_fields = ("payouts_account",)

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> Sequence[str]:
        if obj is not None:
            assert isinstance(obj, PayoutParty)
            if Payout.objects.filter(Q(recipient=obj) | Q(payer=obj)).exists():
                return self.fields  # type: ignore
        return self.readonly_fields


class RefundAdmin(PayoutAdmin):
    raw_id_fields = [
        "account",
        "parent",
        "payer",
        "recipient",
    ]
    fields = [
        "connection",
        "account",
        "payer",
        "parent",
        "recipient",
        "amount",
        "messages",
        "reference",
        "attachment",
        "msg_id",
        "file_name",
        "timestamp",
        "paid_date",
        "group_status",
        "created",
    ]
    readonly_fields = [
        "msg_id",
        "file_name",
        "timestamp",
        "paid_date",
        "group_status",
        "created",
    ]
    inlines = [AccountEntryNoteInline]


class CurrencyExchangeSourceAdmin(BankAdminBase):
    save_on_top = False
    exclude = ()

    fields = (
        "id",
        "created",
        "name",
    )

    readonly_fields = (
        "id",
        "created",
    )

    list_display = fields


class CurrencyExchangeAdmin(BankAdminBase):
    save_on_top = False

    fields = (
        "record_date",
        "source_currency",
        "target_currency",
        "unit_currency",
        "exchange_rate",
        "source",
    )

    date_hierarchy = "record_date"
    readonly_fields = list_display = fields
    raw_id_fields = ("source",)
    list_filter = ("source_currency", "target_currency", "source")


class WsEdiConnectionAdmin(BankAdminBase):
    save_on_top = False

    ordering = [
        "name",
    ]

    list_display = (
        "id",
        "created",
        "name",
        "sender_identifier",
        "receiver_identifier",
        "expires",
    )

    raw_id_fields = ()

    fieldsets = (
        (
            None,
            {
                "fields": [
                    "id",
                    "name",
                    "enabled",
                    "sender_identifier",
                    "receiver_identifier",
                    "target_identifier",
                    "signer_identifier",
                    "agreement_identifier",
                    "environment",
                    "debug_commands",
                    "created",
                ]
            },
        ),
        (
            "PKI",
            {
                "fields": [
                    "pki_endpoint",
                    "pin",
                    "bank_root_cert_file",
                ]
            },
        ),
        (
            "EDI",
            {
                "fields": [
                    "soap_endpoint",
                    "signing_cert_file",
                    "signing_key_file",
                    "encryption_cert_file",
                    "encryption_key_file",
                    "bank_encryption_cert_file",
                    "bank_signing_cert_file",
                    "ca_cert_file",
                    "use_sha256",
                    "use_wsse_timestamp",
                ]
            },
        ),
    )

    readonly_fields = (
        "id",
        "created",
        "expires",
    )

    def expires(self, obj: WsEdiConnection) -> str:
        assert isinstance(obj, WsEdiConnection)
        min_not_valid_after: Optional[datetime] = obj.valid_until
        return date_format(min_not_valid_after.date(), "SHORT_DATE_FORMAT") if min_not_valid_after else ""

    expires.short_description = _("expires")  # type: ignore


class WsEdiSoapCallAdmin(BankAdminBase):
    save_on_top = False

    date_hierarchy = "created"

    list_display = (
        "id",
        "created",
        "connection",
        "command",
        "executed",
        "execution_time",
    )

    list_filter = (
        "connection",
        "command",
    )

    raw_id_fields = ()

    fields = (
        "id",
        "connection",
        "command",
        "created",
        "executed",
        "execution_time",
        "error_fmt",
        "admin_application_request",
        "admin_application_response",
        "admin_application_response_file",
    )

    readonly_fields = (
        "id",
        "connection",
        "command",
        "created",
        "executed",
        "execution_time",
        "error_fmt",
        "admin_application_request",
        "admin_application_response",
        "admin_application_response_file",
    )

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if not request.user.is_superuser:
            fields = fields[:-2]
        return fields

    def soap_download_view(self, request, object_id, file_type: str, form_url="", extra_context=None):  # pylint: disable=unused-argument
        user = request.user
        if not user.is_authenticated or not user.is_superuser:
            raise Http404("File not found")
        obj = get_object_or_404(self.get_queryset(request), id=object_id)
        assert isinstance(obj, WsEdiSoapCall)
        if file_type == "f":
            with open(obj.debug_response_full_path, "rb") as fb:
                data = xml_to_dict(fb.read())
                content = base64.b64decode(data.get("Content", ""))
                return FormattedXmlResponse(content, filename=obj.debug_get_filename(file_type))
        return FormattedXmlFileResponse(WsEdiSoapCall.debug_get_file_path(obj.debug_get_filename(file_type)))

    def get_urls(self) -> List[URLPattern]:
        info = self.model._meta.app_label, self.model._meta.model_name  # noqa
        return [
            path("<path:object_id>/soap-download/<path:file_type>/", self.admin_site.admin_view(self.soap_download_view), name="%s_%s_soap_download" % info),
        ] + super().get_urls()

    def admin_application_request(self, obj):
        assert isinstance(obj, WsEdiSoapCall)
        if not os.path.isfile(obj.debug_request_full_path):
            return ""
        download_url = reverse("admin:jbank_wsedisoapcall_soap_download", args=[str(obj.id), "q"])
        return mark_safe(format_html('<a href="{}">{}</a>', download_url, os.path.basename(obj.debug_request_full_path)))

    admin_application_request.short_description = _("application request")  # type: ignore

    def admin_application_response(self, obj):
        assert isinstance(obj, WsEdiSoapCall)
        if not os.path.isfile(obj.debug_response_full_path):
            return ""
        download_url = reverse("admin:jbank_wsedisoapcall_soap_download", args=[str(obj.id), "s"])
        return mark_safe(format_html('<a href="{}">{}</a>', download_url, os.path.basename(obj.debug_response_full_path)))

    admin_application_response.short_description = _("application response")  # type: ignore

    def admin_application_response_file(self, obj):
        assert isinstance(obj, WsEdiSoapCall)
        if obj.command != "DownloadFile" or not obj.executed:
            return ""
        file_type = "f"
        download_url = reverse("admin:jbank_wsedisoapcall_soap_download", args=[str(obj.id), file_type])
        return mark_safe(format_html('<a href="{}">{}</a>', download_url, obj.debug_get_filename(file_type)))

    admin_application_response_file.short_description = _("file")  # type: ignore

    def execution_time(self, obj):
        assert isinstance(obj, WsEdiSoapCall)
        return obj.executed - obj.created if obj.executed else ""

    execution_time.short_description = _("execution time")  # type: ignore

    def error_fmt(self, obj):
        assert isinstance(obj, WsEdiSoapCall)
        return mark_safe(obj.error.replace("\n", "<br>"))

    error_fmt.short_description = _("error")  # type: ignore


class EuriborRateAdmin(BankAdminBase):
    save_on_top = False
    fields = [
        "record_date",
        "name",
        "rate",
        "created",
    ]
    date_hierarchy = "record_date"
    list_filter = [
        "name",
    ]
    list_display = [
        "id",
        "record_date",
        "name",
        "rate",
    ]
    readonly_fields = [
        "id",
        "created",
    ]


class AccountBalanceAdmin(BankAdminBase):
    save_on_top = False
    fields = [
        "id",
        "record_datetime_fmt",
        "account_number",
        "bic",
        "balance_fmt",
        "available_balance_fmt",
        "credit_limit_fmt",
        "currency",
        "created_fmt",
    ]
    date_hierarchy = "record_datetime"
    list_filter = [
        "account_number",
        "bic",
        "currency",
    ]
    list_display = [
        "id",
        "record_datetime_fmt",
        "account_number",
        "bic",
        "balance_fmt",
        "available_balance_fmt",
        "credit_limit_fmt",
        "currency",
        "created_fmt",
    ]
    readonly_fields = fields  # type: ignore

    def has_add_permission(self, request) -> bool:  # type: ignore  # noqa
        return False

    @admin.display(description=_("balance"), ordering="balance")
    def balance_fmt(self, obj):
        assert isinstance(obj, AccountBalance)
        return self.format_currency(obj.balance)

    @admin.display(description=_("available balance"), ordering="available_balance")
    def available_balance_fmt(self, obj):
        assert isinstance(obj, AccountBalance)
        return self.format_currency(obj.available_balance)

    @admin.display(description=_("credit limit"), ordering="available_balance")
    def credit_limit_fmt(self, obj):
        assert isinstance(obj, AccountBalance)
        return self.format_currency(obj.credit_limit)

    @admin.display(description=_("created"), ordering="created")
    def created_fmt(self, obj):
        assert isinstance(obj, AccountBalance)
        return format_timedelta(now() - obj.created)

    @admin.display(description=_("record date"), ordering="record_datetime")
    def record_datetime_fmt(self, obj):
        assert isinstance(obj, AccountBalance)
        return date_format(obj.record_datetime, "SHORT_DATETIME_FORMAT")


mark_as_marked_reconciled.short_description = _("Mark as reconciled")  # type: ignore
unmark_marked_reconciled_flag.short_description = _("Unmark reconciled flag")  # type: ignore
send_payouts_to_bank.short_description = _("Send payouts to bank")  # type: ignore
mark_payouts_as_paid.short_description = _("Mark payouts as paid")  # type: ignore
regenerate_payout_message_identifiers.short_description = _("Regenerate payout message identifiers")  # type: ignore
show_payout_summary.short_description = _("Show payout summary")  # type: ignore

admin.site.register(CurrencyExchangeSource, CurrencyExchangeSourceAdmin)
admin.site.register(CurrencyExchange, CurrencyExchangeAdmin)
admin.site.register(Payout, PayoutAdmin)
admin.site.register(PayoutStatus, PayoutStatusAdmin)
admin.site.register(PayoutParty, PayoutPartyAdmin)
admin.site.register(Refund, RefundAdmin)
admin.site.register(Statement, StatementAdmin)
admin.site.register(StatementRecord, StatementRecordAdmin)
admin.site.register(StatementFile, StatementFileAdmin)
admin.site.register(ReferencePaymentRecord, ReferencePaymentRecordAdmin)
admin.site.register(ReferencePaymentBatch, ReferencePaymentBatchAdmin)
admin.site.register(ReferencePaymentBatchFile, ReferencePaymentBatchFileAdmin)
admin.site.register(WsEdiConnection, WsEdiConnectionAdmin)
admin.site.register(WsEdiSoapCall, WsEdiSoapCallAdmin)
admin.site.register(EuriborRate, EuriborRateAdmin)
admin.site.register(AccountBalance, AccountBalanceAdmin)
