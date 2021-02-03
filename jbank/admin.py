# pylint: disable=too-many-arguments
import base64
import logging
import os
import traceback
from datetime import datetime
from os.path import basename
from typing import Optional, Sequence
import pytz
from django import forms
from django.conf import settings
from django.conf.urls import url
from django.contrib import admin
from django.contrib import messages
from django.contrib.admin import SimpleListFilter
from django.contrib.auth.models import User
from django.contrib.messages import add_message, ERROR
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import transaction
from django.db.models import F, Q
from django.db.models.aggregates import Sum
from django.http import HttpRequest, Http404
from django.shortcuts import render, get_object_or_404
from django.urls import ResolverMatch, reverse
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _
from jacc.models import Account, EntryType
from jbank.x509_helpers import get_x509_cert_from_file
from jutil.request import get_ip
from jutil.responses import FormattedXmlResponse, FormattedXmlFileResponse
from jutil.xml import xml_to_dict
from jbank.helpers import create_statement, create_reference_payment_batch
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
)
from jbank.parsers import (
    parse_tiliote_statements,
    parse_tiliote_statements_from_file,
    parse_svm_batches_from_file,
    parse_svm_batches,
)
from jutil.admin import ModelAdminBase, admin_log, admin_log_changed_fields

logger = logging.getLogger(__name__)


class BankAdminBase(ModelAdminBase):
    def save_form(self, request, form, change):
        if change:
            admin_log_changed_fields(form.instance, form.changed_data, request.user, ip=get_ip(request))
        return form.save(commit=False)


class SettlementEntryTypesFilter(SimpleListFilter):
    """
    Filters incoming settlement type entries.
    """

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
    """
    Filters incoming payments which do not have any child/derived account entries.
    """

    title = _("account.entry.matched.filter")
    parameter_name = "matched"

    def lookups(self, request, model_admin):
        return [
            ("1", capfirst(_("account.entry.not.matched"))),
            ("2", capfirst(_("account.entry.is.matched"))),
            ("3", capfirst(_("marked as settled"))),
        ]

    def queryset(self, request, queryset):
        val = self.value()
        if val:
            # return original settlements only
            queryset = queryset.filter(type__is_settlement=True, parent=None)
            if val == "1":
                # return those which are not manually settled and
                # have either a) no children b) sum of children less than amount
                queryset = queryset.exclude(manually_settled=True)
                queryset = queryset.annotate(child_set_amount=Sum("child_set__amount"))
                return queryset.filter(Q(child_set=None) | Q(child_set_amount__lt=F("amount")))
            if val == "2":
                # return any entries with derived account entries or marked as manually settled
                return queryset.exclude(Q(child_set=None) & Q(manually_settled=False))
            if val == "3":
                # return only manually marked as settled
                return queryset.filter(manually_settled=True)
        return queryset


class AccountNameFilter(SimpleListFilter):
    """
    Filters account entries based on account name.
    """

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
    date_hierarchy = "record_date"
    list_filter = ("account_number",)
    readonly_fields = (
        "file_link",
        "account_number",
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
        "record_date",
        "account_number",
        "statement_number",
        "begin_balance",
        "currency_code",
        "file_link",
        "account_entry_list",
    )

    def account_entry_list(self, obj):
        assert isinstance(obj, Statement)
        admin_url = reverse("admin:jbank_statementrecord_statement_changelist", args=(obj.id,))
        return format_html(
            "<a href='{}'>{}</a>", mark_safe(admin_url), StatementRecord.objects.filter(statement=obj).count()
        )

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
            url(
                r"^by-file/(?P<file_id>\d+)/$",
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

    def has_add_permission(self, request, obj=None):  # pylint: disable=unused-argument
        return False


def mark_as_manually_settled(modeladmin, request, qs):  # pylint: disable=unused-argument
    try:
        data = request.POST.dict()

        if "description" in data:
            description = data["description"]
            user = request.user
            for e in list(qs.filter(manually_settled=False)):
                e.manually_settled = True
                e.save(update_fields=["manually_settled"])
                msg = "{}: {}".format(capfirst(_("marked as manually settled")), description)
                admin_log([e], msg, who=user)
                messages.info(request, msg)
        else:
            cx = {
                "qs": qs,
            }
            return render(request, "admin/jbank/mark_as_manually_settled.html", context=cx)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    except Exception as e:
        logger.error("mark_as_manually_settled: %s", traceback.format_exc())
        messages.error(request, "{}".format(e))
    return None


def unmark_manually_settled_flag(modeladmin, request, qs):  # pylint: disable=unused-argument
    user = request.user
    for e in list(qs.filter(manually_settled=True)):
        e.manually_settled = False
        e.save(update_fields=["manually_settled"])
        msg = capfirst(_("manually settled flag cleared"))
        admin_log([e], msg, who=user)
        messages.info(request, msg)


class StatementRecordAdmin(BankAdminBase):
    exclude = ()
    list_per_page = 25
    save_on_top = False
    date_hierarchy = "record_date"
    readonly_fields = (
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
        "archived",
        "manually_settled",
        # from AccountEntry
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
    )
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
        AccountEntryMatchedFilter,
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
        "record_date",
        "type",
        "record_code",
        "amount",
        "name",
        "recipient_account_number",
        "remittance_info",
        "source_file_link",
    )
    inlines = (
        StatementRecordSepaInfoInlineAdmin,
        StatementRecordDetailInlineAdmin,
    )
    actions = (
        mark_as_manually_settled,
        unmark_manually_settled_flag,
    )

    def get_urls(self):
        return [
            url(
                r"^by-statement/(?P<statement_id>\d+)/$",
                self.admin_site.admin_view(self.kw_changelist_view),
                name="jbank_statementrecord_statement_changelist",
            ),
        ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        statement_id = rm.kwargs.get("statement_id", None)
        if statement_id:
            qs = qs.filter(statement__id=statement_id)
        return qs

    def source_file_link(self, obj):
        assert isinstance(obj, StatementRecord)
        if not obj.statement:
            return ""
        admin_url = reverse("admin:jbank_statementfile_change", args=(obj.statement.file.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.statement.name)

    source_file_link.admin_order_field = "statement"  # type: ignore
    source_file_link.short_description = _("source file")  # type: ignore

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
    readonly_fields = (
        "id",
        "batch",
        "line_number",
        "file_link",
        "record_type",
        "account_number",
        "record_date",
        "paid_date",
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
        "manually_settled",
        # from AccountEntry
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
    )
    actions = (
        mark_as_manually_settled,
        unmark_manually_settled_flag,
    )

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
            url(
                r"^by-batch/(?P<batch_id>\d+)/$",
                self.admin_site.admin_view(self.kw_changelist_view),
                name="jbank_referencepaymentrecord_batch_changelist",
            ),
        ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        batch_id = rm.kwargs.get("batch_id", None)
        if batch_id:
            qs = qs.filter(batch_id=batch_id)
        return qs

    def source_file_link(self, obj):
        assert isinstance(obj, ReferencePaymentRecord)
        if not obj.batch:
            return ""
        admin_url = reverse("admin:jbank_referencepaymentbatchfile_change", args=(obj.batch.file.id,))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.batch.name)

    source_file_link.admin_order_field = "batch"  # type: ignore
    source_file_link.short_description = _("account entry source file")  # type: ignore


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
        "name",
        "record_date",
        "service_identifier",
        "currency_identifier",
        "account_entry_list",
    )

    def account_entry_list(self, obj):
        assert isinstance(obj, ReferencePaymentBatch)
        admin_url = reverse("admin:jbank_referencepaymentrecord_batch_changelist", args=(obj.id,))
        return format_html(
            "<a href='{}'>{}</a>", mark_safe(admin_url), ReferencePaymentRecord.objects.filter(batch=obj).count()
        )

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
            url(
                r"^by-file/(?P<file_id>\d+)/$",
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
        except Exception as e:
            raise ValidationError(_("Unhandled error") + ": {}".format(e))
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
    )

    readonly_fields = (
        "created",
        "errors",
        "file",
        "original_filename",
    )

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
        except Exception as e:
            raise ValidationError(_("Unhandled error") + ": {}".format(e))
        return file


class ReferencePaymentBatchFileAdmin(BankAdminBase):
    save_on_top = False
    exclude = ()
    form = ReferencePaymentBatchFileForm
    date_hierarchy = "created"

    list_display = (
        "id",
        "created",
        "file",
    )

    list_filter = ("tag",)

    search_fields = ("file__contains",)

    readonly_fields = (
        "created",
        "errors",
        "file",
        "original_filename",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

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


class PayoutStatusAdmin(BankAdminBase):
    fields = (
        "created",
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
    list_display = (
        "id",
        "created",
        "payout",
        "file_name_link",
        "response_code",
        "response_text",
        "original_msg_id",
        "group_status",
    )

    def file_download_view(
        self, request, pk, filename, form_url="", extra_context=None
    ):  # pylint: disable=unused-argument
        user = request.user
        if not user.is_authenticated or not user.is_staff:
            raise Http404(_("File {} not found").format(filename))
        obj = get_object_or_404(self.get_queryset(request), pk=pk, file_name=filename)
        assert isinstance(obj, PayoutStatus)
        full_path = obj.full_path
        if not os.path.isfile(full_path):
            raise Http404(_("File {} not found").format(filename))
        return FormattedXmlFileResponse(full_path)

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

    def get_urls(self):
        urls = [
            url(
                r"^(\d+)/change/status-downloads/(.+)/$",
                self.file_download_view,
                name="jbank_payoutstatus_file_download",
            ),
        ]
        return urls + super().get_urls()


class PayoutStatusInlineAdmin(admin.TabularInline):
    model = PayoutStatus
    can_delete = False
    extra = 0
    ordering = ("-id",)
    fields = PayoutStatusAdmin.fields
    readonly_fields = PayoutStatusAdmin.readonly_fields

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


class PayoutAdmin(BankAdminBase):
    save_on_top = False
    exclude = ()
    inlines = [PayoutStatusInlineAdmin]
    date_hierarchy = "timestamp"

    raw_id_fields: Sequence[str] = (
        "account",
        "parent",
        "payer",
        "recipient",
    )

    list_filter: Sequence[str] = (
        "state",
        "payoutstatus_set__response_code",
        "payoutstatus_set__group_status",
        "recipient__bic",
    )

    fields: Sequence[str] = (
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
    )

    list_display: Sequence[str] = (
        "id",
        "timestamp",
        "recipient",
        "amount",
        "paid_date",
        "state",
    )

    readonly_fields: Sequence[str] = (
        "created",
        "paid_date",
        "timestamp",
        "msg_id",
        "file_name",
        "group_status",
    )

    search_fields: Sequence[str] = (
        "=msg_id",
        "=file_name",
        "=file_reference",
        "recipient__name",
        "=recipient__account_number",
        "=msg_id",
        "=amount",
    )

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
    exclude = ()
    search_fields = (
        "name",
        "=account_number",
        "=org_id",
    )
    ordering = ("name",)

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


class RefundAdmin(PayoutAdmin):
    raw_id_fields = (
        "account",
        "parent",
        "payer",
        "recipient",
    )
    fields = (
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
    )
    readonly_fields = (
        "msg_id",
        "file_name",
        "timestamp",
        "paid_date",
        "group_status",
        "created",
    )


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
                ]
            },
        ),
    )

    readonly_fields = (
        "id",
        "created",
        "expires",
    )

    def expires(self, obj):
        assert isinstance(obj, WsEdiConnection)
        min_not_valid_after: Optional[datetime] = None
        try:
            certs = [
                obj.signing_cert_full_path,
                obj.encryption_cert_full_path,
                obj.bank_encryption_cert_full_path,
                obj.bank_root_cert_full_path,
                obj.ca_cert_full_path,
            ]
        except Exception as e:
            logger.error(e)
            return _("(missing certificate files)")
        for filename in certs:
            if filename and os.path.isfile(filename):
                cert = get_x509_cert_from_file(filename)
                not_valid_after = pytz.utc.localize(cert.not_valid_after)
                if min_not_valid_after is None or not_valid_after < min_not_valid_after:
                    min_not_valid_after = not_valid_after
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

    def soap_download_view(
        self, request, object_id, file_type, form_url="", extra_context=None
    ):  # pylint: disable=unused-argument
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

    def admin_application_request(self, obj):
        assert isinstance(obj, WsEdiSoapCall)
        if not os.path.isfile(obj.debug_request_full_path):
            return ""
        download_url = reverse("admin:jbank_wsedisoapcall_soap_download", args=[str(obj.id), "a"])
        return mark_safe(
            format_html('<a href="{}">{}</a>', download_url, os.path.basename(obj.debug_request_full_path))
        )

    admin_application_request.short_description = _("application request")  # type: ignore

    def admin_application_response(self, obj):
        assert isinstance(obj, WsEdiSoapCall)
        if not os.path.isfile(obj.debug_response_full_path):
            return ""
        download_url = reverse("admin:jbank_wsedisoapcall_soap_download", args=[str(obj.id), "r"])
        return mark_safe(
            format_html('<a href="{}">{}</a>', download_url, os.path.basename(obj.debug_response_full_path))
        )

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

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        return [
            url(r"^soap-download/(\d+)/(.+)$", self.soap_download_view, name="%s_%s_soap_download" % info),
        ] + super().get_urls()


mark_as_manually_settled.short_description = _("Mark as manually settled")  # type: ignore
unmark_manually_settled_flag.short_description = _("Unmark manually settled flag")  # type: ignore

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
