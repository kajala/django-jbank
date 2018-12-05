import logging
import traceback
from os.path import basename, join

from django import forms
from django.conf import settings
from django.conf.urls import url
from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django.contrib.auth.models import User
from django.contrib.messages import add_message, ERROR
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import transaction
from django.http import HttpRequest
from django.urls import ResolverMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.text import format_lazy, capfirst
from django.utils.translation import ugettext_lazy as _
from jacc.admin import AccountTypeAccountEntryFilter
from jacc.models import Account, EntryType
from jbank.helpers import create_statement, create_reference_payment_batch
from jbank.models import Statement, StatementRecord, StatementRecordSepaInfo, ReferencePaymentRecord, \
    ReferencePaymentBatch, StatementFile, ReferencePaymentBatchFile, Payout, Refund, PayoutStatus, PayoutParty
from jbank.parsers import parse_tiliote_statements, parse_tiliote_statements_from_file, parse_svm_batches_from_file, \
    parse_svm_batches
from jutil.admin import ModelAdminBase, AdminFileDownloadMixin

logger = logging.getLogger(__name__)


class SettlementEntryTypesFilter(SimpleListFilter):
    """
    Filters incoming settlement type entries.
    """
    title = _('account entry types')
    parameter_name = 'type'

    def lookups(self, request, model_admin):
        choices = []
        for e in EntryType.objects.all().filter(is_settlement=True).order_by('name'):
            assert isinstance(e, EntryType)
            choices.append((e.id, capfirst(e.name)))

    def queryset(self, request, queryset):
        val = self.value()
        if val:
            return queryset.filter(type__id=val)
        return queryset


class AccountEntryMatchedFilter(SimpleListFilter):
    """
    Filters incoming payments which do not have any child/derived account entries.
    """
    title = _('account.entry.matched.filter')
    parameter_name = 'matched'

    def lookups(self, request, model_admin):
        return [
            ('1', capfirst(_('account.entry.not.matched'))),
            ('2', capfirst(_('account.entry.is.matched'))),
        ]

    def queryset(self, request, queryset):
        val = self.value()
        if val:
            queryset = queryset.filter(type__is_settlement=True, parent=None)
            if val == '1':
                return queryset.filter(child_set=None)
            elif val == '2':
                return queryset.exclude(child_set=None)
        return queryset


class StatementAdmin(ModelAdminBase):
    exclude = ()
    list_per_page = 20
    save_on_top = False
    ordering = ('-record_date', 'account_number')
    date_hierarchy = 'record_date'
    list_filter = (
        'account_number',
    )
    readonly_fields = (
        'file_link',
        'account_number',
        'statement_number',
        'begin_date',
        'end_date',
        'record_date',
        'customer_identifier',
        'begin_balance_date',
        'begin_balance',
        'record_count',
        'currency_code',
        'account_name',
        'account_limit',
        'owner_name',
        'contact_info_1',
        'contact_info_2',
        'bank_specific_info_1',
        'iban',
        'bic',
    )
    fields = readonly_fields
    search_fields = (
        'name',
        'statement_number',
    )
    list_display = (
        'id',
        'record_date',
        'account_number',
        'statement_number',
        'begin_balance',
        'currency_code',
        'name',
        'account_entry_list'
    )

    def account_entry_list(self, obj):
        assert isinstance(obj, Statement)
        admin_url = reverse('admin:jbank_statementrecord_statement_changelist', args=(obj.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), StatementRecord.objects.filter(statement=obj).count())
    account_entry_list.short_description = _('account entries')

    def file_link(self, obj):
        assert isinstance(obj, Statement)
        if not obj.file:
            return ''
        admin_url = reverse('admin:jbank_statementfile_change', args=(obj.file.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.name)
    file_link.admin_order_field = 'file'
    file_link.short_description = _('file')

    def get_urls(self):
        return [
                url(r'^by-file/(?P<file_id>\d+)/$', self.admin_site.admin_view(self.kw_changelist_view), name='jbank_statement_file_changelist'),
            ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        file_id = rm.kwargs.get('file_id', None)
        if file_id:
            qs = qs.filter(file=file_id)
        return qs


class StatementRecordSepaInfoInlineAdmin(admin.StackedInline):
    exclude = ()
    model = StatementRecordSepaInfo
    can_delete = False
    extra = 0
    readonly_fields = (
        'record',
        'reference',
        'iban_account_number',
        'bic_code',
        'recipient_name_detail',
        'payer_name_detail',
        'identifier',
        'archive_identifier',
    )
    raw_id_fields = (
        'record',
    )


class StatementRecordAdmin(ModelAdminBase):
    exclude = ()
    list_per_page = 50
    save_on_top = False
    date_hierarchy = 'record_date'
    readonly_fields = (
        'id',
        'statement',
        'file_link',
        'record_number',
        'archive_identifier',
        'record_date',
        'value_date',
        'paid_date',
        'type',
        'record_code',
        'record_description',
        'amount',
        'receipt_code',
        'delivery_method',
        'name',
        'name_source',
        'recipient_account_number',
        'recipient_account_number_changed',
        'remittance_info',
        'messages',
        'client_messages',
        'bank_messages',
        # from AccountEntry
        'account',
        'timestamp',
        'created',
        'last_modified',
        'type',
        'description',
        'amount',
        'source_file',
        'source_invoice',
        'settled_invoice',
        'settled_item',
        'parent',
    )
    raw_id_fields = (
        'statement',
        # from AccountEntry
        'account',
        'source_file',
        'parent',
        'source_invoice',
        'settled_invoice',
        'settled_item',
    )
    list_filter = (
        AccountEntryMatchedFilter,
        SettlementEntryTypesFilter,
        'record_code',
    )
    search_fields = (
        '=archive_identifier',
        '=amount',
        '=recipient_account_number',
        'record_description',
        'name',
        'remittance_info',
    )
    list_display = (
        'id',
        'record_date',
        'type',
        'record_code',
        'amount',
        'name',
        'recipient_account_number',
        'remittance_info',
        'source_file_link'
    )
    inlines = (
        StatementRecordSepaInfoInlineAdmin,
    )

    def get_urls(self):
        return [
                url(r'^by-statement/(?P<statement_id>\d+)/$', self.admin_site.admin_view(self.kw_changelist_view), name='jbank_statementrecord_statement_changelist'),
            ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        statement_id = rm.kwargs.get('statement_id', None)
        if statement_id:
            qs = qs.filter(statement__id=statement_id)
        return qs

    def source_file_link(self, obj):
        assert isinstance(obj, StatementRecord)
        if not obj.statement:
            return ''
        admin_url = reverse('admin:jbank_statement_change', args=(obj.statement.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.statement.name)
    source_file_link.admin_order_field = 'statement'
    source_file_link.short_description = _('account entry source file')

    def file_link(self, obj):
        assert isinstance(obj, StatementRecord)
        if not obj.statement or not obj.statement.file:
            return ''
        name = basename(obj.statement.file.file.name)
        admin_url = reverse('admin:jbank_statementfile_change', args=(obj.statement.file.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), name)
    file_link.admin_order_field = 'file'
    file_link.short_description = _("account statement file")


class ReferencePaymentRecordAdmin(ModelAdminBase):
    exclude = ()
    list_per_page = 50
    save_on_top = False
    date_hierarchy = 'record_date'
    raw_id_fields = (
        'batch',
        # from AccountEntry
        'account',
        'source_file',
        'parent',
        'source_invoice',
        'settled_invoice',
        'settled_item',
    )
    readonly_fields = (
        'id',
        'batch',
        'file_link',
        'record_type',
        'account_number',
        'record_date',
        'paid_date',
        'archive_identifier',
        'remittance_info',
        'payer_name',
        'currency_identifier',
        'name_source',
        'amount',
        'correction_identifier',
        'delivery_method',
        'receipt_code',
        # from AccountEntry
        'account',
        'timestamp',
        'created',
        'last_modified',
        'type',
        'description',
        'amount',
        'source_file',
        'source_invoice',
        'settled_invoice',
        'settled_item',
        'parent',
    )
    list_filter = (
        AccountEntryMatchedFilter,
        'correction_identifier',
        'receipt_code',
    )
    search_fields = (
        '=archive_identifier',
        '=amount',
        'remittance_info',
        'payer_name',
        'batch__name',
    )
    list_display = (
        'id',
        'record_date',
        'type',
        'amount',
        'payer_name',
        'remittance_info',
        'source_file_link',
    )

    def file_link(self, obj):
        assert isinstance(obj, ReferencePaymentRecord)
        if not obj.batch or not obj.batch.file:
            return ''
        admin_url = reverse('admin:jbank_referencepaymentbatchfile_change', args=(obj.batch.file.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.batch.file)
    file_link.admin_order_field = 'file'
    file_link.short_description = _('file')

    def get_urls(self):
        return [
                url(r'^by-batch/(?P<batch_id>\d+)/$', self.admin_site.admin_view(self.kw_changelist_view), name='jbank_referencepaymentrecord_batch_changelist'),
            ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        batch_id = rm.kwargs.get('batch_id', None)
        if batch_id:
            qs = qs.filter(batch_id=batch_id)
        return qs

    def source_file_link(self, obj):
        assert isinstance(obj, ReferencePaymentRecord)
        if not obj.batch:
            return ''
        admin_url = reverse('admin:jbank_referencepaymentbatchfile_change', args=(obj.batch.file.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.batch.name)
    source_file_link.admin_order_field = 'batch'
    source_file_link.short_description = _('account entry source file')


class ReferencePaymentBatchAdmin(ModelAdminBase):
    exclude = ()
    list_per_page = 20
    save_on_top = False
    ordering = ('-record_date',)
    date_hierarchy = 'record_date'
    list_filter = (
        'record_set__account_number',
    )
    fields = (
        'file_link',
        'record_date',
        'institution_identifier',
        'service_identifier',
        'currency_identifier',
    )
    readonly_fields = (
        'name',
        'file',
        'file_link',
        'record_date',
        'institution_identifier',
        'service_identifier',
        'currency_identifier',
    )
    search_fields = (
        'name',
        '=record_set__archive_identifier',
        '=record_set__amount',
        'record_set__remittance_info',
        'record_set__payer_name',
    )
    list_display = (
        'id',
        'name',
        'record_date',
        'service_identifier',
        'currency_identifier',
        'account_entry_list',
    )

    def account_entry_list(self, obj):
        assert isinstance(obj, ReferencePaymentBatch)
        admin_url = reverse('admin:jbank_referencepaymentrecord_batch_changelist', args=(obj.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), ReferencePaymentRecord.objects.filter(batch=obj).count())
    account_entry_list.short_description = _('account entries')

    def file_link(self, obj):
        assert isinstance(obj, ReferencePaymentBatch)
        if not obj.file:
            return ''
        admin_url = reverse('admin:jbank_referencepaymentbatchfile_change', args=(obj.file.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), obj.file)
    file_link.admin_order_field = 'file'
    file_link.short_description = _('file')

    def get_urls(self):
        return [
                url(r'^by-file/(?P<file_id>\d+)/$', self.admin_site.admin_view(self.kw_changelist_view), name='jbank_referencepaymentbatch_file_changelist'),
            ] + super().get_urls()

    def get_queryset(self, request: HttpRequest):
        rm = request.resolver_match
        assert isinstance(rm, ResolverMatch)
        qs = super().get_queryset(request)
        file_id = rm.kwargs.get('file_id', None)
        if file_id:
            qs = qs.filter(file=file_id)
        return qs


class StatementFileForm(forms.ModelForm):
    class Meta:
        exclude = []

    def clean_file(self):
        file = self.cleaned_data['file']
        assert isinstance(file, InMemoryUploadedFile)
        name = file.name
        file.seek(0)
        content = file.read()
        assert isinstance(content, bytes)
        try:
            statements = parse_tiliote_statements(content.decode('ISO-8859-1'), filename=basename(name))
            for stm in statements:
                account_number = stm['header']['account_number']
                if Account.objects.filter(name=account_number).count() == 0:
                    raise ValidationError(_('account.not.found').format(account_number=account_number))
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(_('Unhandled error') + ': {}'.format(e))
        return file


class StatementFileAdmin(ModelAdminBase, AdminFileDownloadMixin):
    save_on_top = False
    exclude = ()
    form = StatementFileForm

    list_display = (
        'id',
        'created',
        'file_link',
    )

    readonly_fields = (
        'created',
        'errors',
        'file_link',
    )

    def file_link(self, obj):
        assert isinstance(obj, StatementFile)
        if not obj.file:
            return ''
        name = basename(obj.file.name)
        admin_url = reverse('admin:jbank_statement_file_changelist', args=(obj.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), name)
    file_link.admin_order_field = 'file'
    file_link.short_description = _('statements')

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

    def get_urls(self):
        return self.get_download_urls() + super().get_urls()


class ReferencePaymentBatchFileForm(forms.ModelForm):
    class Meta:
        exclude = []

    def clean_file(self):
        file = self.cleaned_data['file']
        assert isinstance(file, InMemoryUploadedFile)
        name = file.name
        file.seek(0)
        content = file.read()
        assert isinstance(content, bytes)
        try:
            batches = parse_svm_batches(content.decode('ISO-8859-1'), filename=basename(name))
            for b in batches:
                for rec in b['records']:
                    account_number = rec['account_number']
                    if Account.objects.filter(name=account_number).count() == 0:
                        raise ValidationError(_('account.not.found').format(account_number=account_number))
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(_('Unhandled error') + ': {}'.format(e))
        return file


class ReferencePaymentBatchFileAdmin(ModelAdminBase, AdminFileDownloadMixin):
    save_on_top = False
    exclude = ()
    form = ReferencePaymentBatchFileForm

    list_display = (
        'id',
        'created',
        'file_link',
    )

    search_fields = (
        'file__contains',
    )

    readonly_fields = (
        'created',
        'errors',
        'file_link',
    )

    def file_link(self, obj):
        assert isinstance(obj, ReferencePaymentBatchFile)
        if not obj.file:
            return ''
        name = basename(obj.file.name)
        admin_url = reverse('admin:jbank_referencepaymentbatch_file_changelist', args=(obj.id, ))
        return format_html("<a href='{}'>{}</a>", mark_safe(admin_url), name)
    file_link.admin_order_field = 'file'
    file_link.short_description = _("reference payment batches")

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
                    logger.error('{}: '.format(plain_filename) + msg)
                    add_message(request, ERROR, msg)
                    instance.delete()

        return super().construct_change_message(request, form, formsets, add)

    def get_urls(self):
        return self.get_download_urls() + super().get_urls()


class PayoutStatusInlineAdmin(admin.TabularInline):
    exclude = ()
    model = PayoutStatus
    can_delete = False
    extra = 0
    ordering = ('-id',)
    readonly_fields = (
        'created',
        'file_name',
        'response_code',
        'response_text',
        'msg_id',
        'original_msg_id',
        'group_status',
        'status_reason',
    )


class PayoutAdmin(ModelAdminBase):
    save_on_top = False
    exclude = ()
    inlines = [PayoutStatusInlineAdmin]
    date_hierarchy = 'timestamp'

    raw_id_fields = (
        'account',
        'parent',
        'payer',
        'recipient',
    )

    list_filter = (
        'state',
        'payoutstatus_set__response_code',
        'payoutstatus_set__group_status',
    )

    fields = (
        'account',
        'parent',
        'payer',
        'recipient',
        'amount',
        'messages',
        'reference',
        'msg_id',
        'file_name',
        'timestamp',
        'due_date',
        'paid_date',
        'state',
        'group_status',
        'created',
    )

    list_display = (
        'id',
        'timestamp',
        'recipient',
        'amount',
        'paid_date',
        'state',
    )

    readonly_fields = (
        'created',
        'msg_id',
        'file_name',
        'group_status',
    )

    search_fields = (
        '=msg_id',
        '=file_name',
        '=file_reference',
    )

    def save_model(self, request, obj, form, change):
        assert isinstance(obj, Payout)
        if not change:
            if not hasattr(obj, 'account') or not obj.account:
                obj.account = obj.payer.payouts_account
            if not hasattr(obj, 'type') or not obj.type:
                obj.type = EntryType.objects.get(code=settings.E_BANK_PAYOUT)
        return super().save_model(request, obj, form, change)


class PayoutPartyAdmin(ModelAdminBase):
    save_on_top = False
    exclude = ()

    list_display = (
        'id',
        'name',
        'account_number',
        'bic',
        'org_id',
        'address',
        'country_code',
    )

    raw_id_fields = (
        'payouts_account',
    )


admin.site.register(Payout, PayoutAdmin)
admin.site.register(PayoutParty, PayoutPartyAdmin)
admin.site.register(Refund, PayoutAdmin)
admin.site.register(Statement, StatementAdmin)
admin.site.register(StatementRecord, StatementRecordAdmin)
admin.site.register(StatementFile, StatementFileAdmin)
admin.site.register(ReferencePaymentRecord, ReferencePaymentRecordAdmin)
admin.site.register(ReferencePaymentBatch, ReferencePaymentBatchAdmin)
admin.site.register(ReferencePaymentBatchFile, ReferencePaymentBatchFileAdmin)
