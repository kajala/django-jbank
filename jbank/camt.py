import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Tuple, Any, Optional, Dict, List

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from jacc.models import Account, EntryType
from jutil.format import dec2, dec4
from jutil.model import clone_model
from jutil.parse import parse_datetime
from jbank.models import (
    StatementFile,
    Statement,
    StatementRecord,
    DELIVERY_FROM_BANK_SYSTEM,
    StatementRecordDetail,
    CurrencyExchange,
    StatementRecordRemittanceInfo,
    CurrencyExchangeSource,
    ReferencePaymentBatchFile,
    ReferencePaymentBatch,
    ReferencePaymentRecord,
)
from jbank.parsers import parse_filename_suffix
from jutil.xml import xml_to_dict


CAMT053_FILE_SUFFIXES = ("XML", "XT", "CAMT", "NDCAMT53L", "XML", "NDARSTXMLO", "NDAREXXMLO", "053")

CAMT053_ARRAY_TAGS = ["Bal", "Ntry", "NtryDtls", "TxDtls", "Strd", "Ustrd"]

CAMT053_INT_TAGS = ["NbOfNtries", "NbOfTxs"]

CAMT054_FILE_SUFFIXES = ("XE", "XE", "CAMT", "NDCAMT54L", "XML", "NDCAPXMLD54O", "NDARCRAXMLO", "054")

CAMT054_ARRAY_TAGS = ["Ntfctn", "Othr", "Ntry", "NtryDtls", "TxDtls", "PrtryAmt", "Chrgs", "AdrLine", "Strd", "Ustrd", "RfrdDocInf", "AddtlRmtInf"]

CAMT054_INT_TAGS = ["NbOfNtries", "NbOfTxs"]

logger = logging.getLogger(__name__)


def camt053_get_iban(data: dict) -> str:
    return data.get("BkToCstmrStmt", {}).get("Stmt", {}).get("Acct", {}).get("Id", {}).get("IBAN", "")


def camt053_get_account_iban(data: dict) -> str:
    return data.get("BkToCstmrStmt", {}).get("Stmt", {}).get("Acct", {}).get("Id", {}).get("IBAN", "")


def camt053_get_account_currency(data: dict) -> str:
    return data.get("BkToCstmrStmt", {}).get("Stmt", {}).get("Acct", {}).get("Ccy") or ""


def camt053_get_val(data: dict, key: str, default: Any = None, required: bool = True, name: str = "") -> Any:
    if key not in data:
        if required:
            raise ValidationError(_("camt.053 field {} missing").format(name if name else key))
        return default
    return data[key]


def camt053_get_str(data: dict, key: str, default: str = "", required: bool = True, name: str = "") -> str:
    return str(camt053_get_val(data, key, default, required, name))


def camt053_get_currency(data: dict, key: str, required: bool = True, name: str = "") -> Tuple[Optional[Decimal], str]:
    try:
        v = camt053_get_val(data, key, default=None, required=False, name=name)
        if v is not None:
            amount = dec2(v["@"])
            currency_code = v["@Ccy"]
            return amount, currency_code
    except Exception:
        pass
    if required:
        raise ValidationError(_("camt.053 field {} type {} missing or invalid").format(name, "currency"))
    return None, ""


def camt053_get_dt(data: Dict[str, Any], key: str, name: str = "") -> datetime:
    s = camt053_get_val(data, key, None, True, name)
    val = parse_datetime(s)
    if val is None:
        raise ValidationError(_("camt.053 field {} type {} missing or invalid").format(name, "datetime") + ": {}".format(s))
    return val


def camt053_get_date_or_none(data: Dict[str, Any], key: str, name: str = "") -> Optional[date]:
    s = camt053_get_val(data, key, None, False, name)
    if not s:
        return None
    val = parse_datetime(s)
    return val.date()


def camt053_get_int(data: Dict[str, Any], key: str, name: str = "") -> int:
    s = camt053_get_val(data, key, None, True, name)
    try:
        return int(s)
    except Exception:
        pass
    raise ValidationError(_("camt.053 field {} type {} missing or invalid").format(name, "int"))


def camt053_get_int_or_none(data: Dict[str, Any], key: str, name: str = "") -> Optional[int]:
    s = camt053_get_val(data, key, None, False, name)
    if s is None:
        return None
    try:
        return int(s)
    except Exception:
        pass
    raise ValidationError(_("camt.053 field {} type {} missing or invalid").format(name, "int"))


def camt053_get_date(data: dict, key: str, default: Optional[date] = None, required: bool = True, name: str = "") -> date:
    s = camt053_get_val(data, key, default, required, name)
    try:
        val = parse_date(s[:10])
        if val is None:
            raise ValidationError(_("camt.053 field {} type {} missing or invalid").format(name, "date"))
        assert isinstance(val, date)
        return val
    except Exception:
        pass
    raise ValidationError(_("camt.053 field {} type {} missing or invalid").format(name, "date") + ": {}".format(s))


def camt053_parse_statement_from_file(filename: str) -> dict:
    if parse_filename_suffix(filename).upper() not in CAMT053_FILE_SUFFIXES:
        raise ValidationError(
            _('File {filename} has unrecognized ({suffixes}) suffix for file type "{file_type}"').format(
                filename=filename, suffixes=", ".join(CAMT053_FILE_SUFFIXES), file_type="camt.053"
            )
        )
    with open(filename, "rb") as fp:
        data = xml_to_dict(fp.read(), array_tags=CAMT053_ARRAY_TAGS, int_tags=CAMT053_INT_TAGS)
        return data


def camt053_get_stmt_bal(d_stmt: dict, bal_type: str) -> Tuple[Decimal, Optional[date]]:
    for bal in d_stmt.get("Bal", []):
        if bal.get("Tp", {}).get("CdOrPrtry", {}).get("Cd", "") == bal_type:
            amt = Decimal(bal.get("Amt", {}).get("@", ""))
            dt_data = bal.get("Dt", {})
            dt = None
            if "Dt" in dt_data:
                dt = camt053_get_date(dt_data, "Dt", name="Stmt.Bal[{}].Dt.Dt".format(bal_type))
            return amt, dt
    raise ValidationError(_("camt.053 field {} type {} missing or invalid").format("Stmt.Bal.Tp.CdOrPrty.Cd", bal_type))


def camt053_domain_from_record_code(record_domain: str) -> str:
    if record_domain == "PMNT":
        return "700"
    if record_domain == "LDAS":
        return "761"
    return ""


def camt053_get_unified_val(qs, k: str, default: Any) -> Any:
    v = default
    for e in qs:
        v2 = getattr(e, k)
        if v == default:
            v = v2
        elif v and v2 and v2 != v:
            return default
    return v


def camt053_get_unified_str(qs, k: str) -> str:
    return camt053_get_unified_val(qs, k, "")


@transaction.atomic  # noqa
def camt053_create_statement(statement_data: dict, name: str, file: StatementFile, **kw) -> Statement:  # noqa
    """Creates camt.053 Statement from statement data parsed by camt053_parse_statement_from_file()

    Args:
        statement_data: XML data in form of dict
        name: File name of the account statement
        file: Source statement file

    Returns:
        Statement
    """
    account_number = camt053_get_account_iban(statement_data)
    account_currency = camt053_get_account_currency(statement_data)
    if not account_number:
        raise ValidationError("{name}: ".format(name=name) + _("account.not.found").format(account_number=""))
    accounts = list(Account.objects.filter(name=account_number, currency=account_currency))
    if len(accounts) != 1:
        raise ValidationError("{name}: ".format(name=name) + _("account.not.found").format(account_number=account_number) + " (" + str(len(accounts)) + ")")
    account = accounts[0]
    assert isinstance(account, Account)

    d_stmt = statement_data.get("BkToCstmrStmt", {}).get("Stmt", {})
    if not d_stmt:
        raise ValidationError(_("camt.053 field {} type {} missing or invalid").format("Stmt", "element"))
    d_acct = d_stmt.get("Acct", {})
    d_ownr = d_acct.get("Ownr", {})
    d_ntry = d_stmt.get("Ntry", [])
    d_frto = d_stmt.get("FrToDt", {})
    d_txsummary = d_stmt.get("TxsSummry", {})

    if Statement.objects.filter(name=name, account=account).first():
        raise ValidationError("Bank account {} statement {} of processed already".format(account_number, name))
    stm = Statement(name=name, account=account, file=file)
    stm.account_number = stm.iban = account_number
    stm.bic = camt053_get_str(d_acct.get("Svcr", {}).get("FinInstnId", {}), "BIC", name="Stmt.Acct.Svcr.FinInstnId.BIC")
    stm.statement_identifier = camt053_get_str(d_stmt, "Id", name="Stmt.Id")
    stm.statement_number = camt053_get_str(d_stmt, "LglSeqNb", name="Stmt.LglSeqNb")
    stm.record_date = camt053_get_dt(d_stmt, "CreDtTm", name="Stmt.CreDtTm")
    stm.begin_date = camt053_get_date_or_none(d_frto, "FrDtTm", name="Stmt.FrDtTm")
    stm.end_date = camt053_get_date_or_none(d_frto, "ToDtTm", name="Stmt.ToDtTm") or stm.record_date
    stm.currency_code = camt053_get_str(d_acct, "Ccy", name="Stmt.Acct.Ccy")
    if stm.currency_code != account.currency:
        raise ValidationError(
            _(
                "Account currency {account_currency} does not match statement entry currency {statement_currency}".format(
                    statement_currency=stm.currency_code, account_currency=account.currency
                )
            )
        )
    stm.owner_name = camt053_get_str(d_ownr, "Nm", name="Stm.Acct.Ownr.Nm")
    stm.begin_balance, stm.begin_balance_date = camt053_get_stmt_bal(d_stmt, "OPBD")
    if stm.begin_balance_date is None:
        stm.begin_balance_date = stm.begin_date
    stm.record_count = camt053_get_int_or_none(d_txsummary.get("TtlNtries", {}), "NbOfNtries", name="Stmt.TxsSummry.TtlNtries.NbOfNtries") or 0
    stm.bank_specific_info_1 = camt053_get_str(d_stmt, "AddtlStmtInf", required=False)[:1024]
    for k, v in kw.items():
        setattr(stm, k, v)
    stm.full_clean()
    stm.save()

    e_deposit = EntryType.objects.filter(code=settings.E_BANK_DEPOSIT).first()
    if not e_deposit:
        raise ValidationError(_("entry.type.missing") + " ({}): {}".format("settings.E_BANK_DEPOSIT", settings.E_BANK_DEPOSIT))
    assert isinstance(e_deposit, EntryType)
    e_withdraw = EntryType.objects.filter(code=settings.E_BANK_WITHDRAW).first()
    if not e_withdraw:
        raise ValidationError(_("entry.type.missing") + " ({}): {}".format("settings.E_BANK_WITHDRAW", settings.E_BANK_WITHDRAW))
    assert isinstance(e_withdraw, EntryType)
    e_types = {
        "CRDT": e_deposit,
        "DBIT": e_withdraw,
    }
    record_type_map = {
        "CRDT": "1",
        "DBIT": "2",
    }

    for ntry in d_ntry:
        archive_id = ntry.get("AcctSvcrRef", "")
        amount, cur = camt053_get_currency(ntry, "Amt", name="Stmt.Ntry[{}].Amt".format(archive_id))
        if cur != account.currency:
            raise ValidationError(
                _(
                    "Account currency {account_currency} does not match statement entry currency {statement_currency}".format(
                        statement_currency=cur, account_currency=account.currency
                    )
                )
            )

        cdt_dbt_ind = ntry["CdtDbtInd"]
        e_type = e_types.get(cdt_dbt_ind, None)
        if not e_type:
            raise ValidationError(_("Statement entry type {} not supported").format(cdt_dbt_ind))

        rec = StatementRecord(statement=stm, account=account, type=e_type)
        rec.amount = amount
        rec.archive_identifier = archive_id
        rec.entry_type = record_type_map[cdt_dbt_ind]
        rec.record_date = record_date = camt053_get_date(ntry.get("BookgDt", {}), "Dt", name="Stmt.Ntry[{}].BkkgDt.Dt".format(archive_id))
        rec.value_date = camt053_get_date(ntry.get("ValDt", {}), "Dt", name="Stmt.Ntry[{}].ValDt.Dt".format(archive_id))
        rec.delivery_method = DELIVERY_FROM_BANK_SYSTEM

        d_bktxcd = ntry.get("BkTxCd", {})
        d_domn = d_bktxcd.get("Domn", {})
        d_family = d_domn.get("Fmly", {})
        d_prtry = d_bktxcd.get("Prtry", {})
        rec.record_domain = record_domain = camt053_get_str(d_domn, "Cd", name="Stmt.Ntry[{}].BkTxCd.Domn.Cd".format(archive_id))
        rec.record_code = camt053_domain_from_record_code(record_domain)
        rec.family_code = camt053_get_str(d_family, "Cd", name="Stmt.Ntry[{}].BkTxCd.Domn.Family.Cd".format(archive_id))
        rec.sub_family_code = camt053_get_str(d_family, "SubFmlyCd", name="Stmt.Ntry[{}].BkTxCd.Domn.Family.SubFmlyCd".format(archive_id))
        rec.record_description = camt053_get_str(d_prtry, "Cd", required=False)

        rec.full_clean()
        rec.save()

        for dtl_batch in ntry.get("NtryDtls", []):
            batch_identifier = dtl_batch.get("Btch", {}).get("MsgId", "")
            dtl_ix = 0
            for dtl in dtl_batch.get("TxDtls", []):
                d = StatementRecordDetail(record=rec, batch_identifier=batch_identifier)

                d_amt_dtl = dtl.get("AmtDtls", {})
                d_txamt = d_amt_dtl.get("TxAmt", {})
                d_xchg = d_txamt.get("CcyXchg", None)

                d.amount, d.currency_code = camt053_get_currency(d_txamt, "Amt", required=False)
                d.instructed_amount, source_currency = camt053_get_currency(d_amt_dtl.get("InstdAmt", {}), "Amt", required=False)
                if (not d_xchg and source_currency and source_currency != d.currency_code) or (d_xchg and not source_currency):
                    raise ValidationError(_("Inconsistent Stmt.Ntry[{}].NtryDtls.TxDtls[{}].AmtDtls".format(archive_id, dtl_ix)))

                if source_currency and source_currency != d.currency_code:
                    source_currency = camt053_get_str(d_xchg, "SrcCcy", default=source_currency, required=False)
                    target_currency = camt053_get_str(d_xchg, "TrgCcy", default=d.currency_code, required=False)
                    unit_currency = camt053_get_str(d_xchg, "UnitCcy", default="", required=False)
                    exchange_rate_str = camt053_get_str(d_xchg, "XchgRate", default="", required=False)
                    exchange_rate = dec4(exchange_rate_str) if exchange_rate_str else None
                    exchange_source = CurrencyExchangeSource.objects.get_or_create(name=account_number)[0]
                    d.exchange = CurrencyExchange.objects.get_or_create(
                        record_date=record_date,
                        source_currency=source_currency,
                        target_currency=target_currency,
                        unit_currency=unit_currency,
                        exchange_rate=exchange_rate,
                        source=exchange_source,
                    )[0]

                d_refs = dtl.get("Refs", {})
                d.archive_identifier = d_refs.get("AcctSvcrRef", "")
                d.end_to_end_identifier = d_refs.get("EndToEndId", "")

                d_parties = dtl.get("RltdPties", {})
                d_dbt = d_parties.get("Dbtr", {})
                d.debtor_name = d_dbt.get("Nm", "")
                d_udbt = d_parties.get("UltmtDbtr", {})
                d.ultimate_debtor_name = d_udbt.get("Nm", "")
                d_cdtr = d_parties.get("Cdtr", {})
                d.creditor_name = d_cdtr.get("Nm", "")
                d_cdtr_acct = d_parties.get("CdtrAcct", {})
                d_cdtr_acct_id = d_cdtr_acct.get("Id", {})
                d.creditor_account = d_cdtr_acct_id.get("IBAN", "")
                if d.creditor_account:
                    d.creditor_account_scheme = "IBAN"
                else:
                    d_cdtr_acct_id_othr = d_cdtr_acct_id.get("Othr") or {}
                    d.creditor_account_scheme = d_cdtr_acct_id_othr.get("SchmeNm", {}).get("Cd", "")
                    d.creditor_account = d_cdtr_acct_id_othr.get("Id") or ""

                d_rmt = dtl.get("RmtInf", {})
                for ustrd in d_rmt.get("Ustrd") or []:
                    if d.unstructured_remittance_info:
                        d.unstructured_remittance_info += "\n"
                    d.unstructured_remittance_info = str(ustrd).rstrip()

                d_rltd_dts = dtl.get("RltdDts", {})
                d.paid_date = camt053_get_dt(d_rltd_dts, "AccptncDtTm") if "AccptncDtTm" in d_rltd_dts else None

                d.full_clean()
                d.save()

                st = StatementRecordRemittanceInfo(detail=d)
                for strd in d_rmt.get("Strd", []):
                    additional_info = strd.get("AddtlRmtInf", "")
                    has_additional_info = bool(additional_info and st.additional_info)
                    amount, currency_code = camt053_get_currency(strd.get("RfrdDocAmt", {}), "RmtdAmt", required=False)
                    has_amount = bool(amount and st.amount)
                    reference = strd.get("CdtrRefInf", {}).get("Ref", "")
                    has_reference = bool(reference and st.reference)

                    # check if new remittance info record is needed
                    if has_additional_info or has_amount or has_reference:
                        st = StatementRecordRemittanceInfo(detail=d)

                    if additional_info:
                        st.additional_info = additional_info
                    if amount:
                        st.amount, st.currency_code = amount, currency_code
                    if reference:
                        st.reference = reference

                    st.full_clean()
                    st.save()

                dtl_ix += 1

        # fill record name from details
        assert rec.type
        if not rec.name:
            if rec.type.code == e_withdraw.code:
                rec.name = camt053_get_unified_str(rec.detail_set.all(), "creditor_name")
            elif rec.type.code == e_deposit.code:
                rec.name = camt053_get_unified_str(rec.detail_set.all(), "debtor_name")
        if not rec.recipient_account_number:
            rec.recipient_account_number = camt053_get_unified_str(rec.detail_set.all(), "creditor_account")
        if not rec.remittance_info:
            rec.remittance_info = camt053_get_unified_str(
                StatementRecordRemittanceInfo.objects.all().filter(detail__record=rec).order_by("id").distinct(), "reference"
            )
        if not rec.paid_date:
            paid_date = camt053_get_unified_val(rec.detail_set.all(), "paid_date", default=None)
            if paid_date:
                assert isinstance(paid_date, datetime)
                rec.paid_date = paid_date.date()

        rec.full_clean()
        rec.save()

    return stm


def camt054_parse_file(filename: str) -> dict:
    if parse_filename_suffix(filename).upper() not in CAMT054_FILE_SUFFIXES:
        raise ValidationError(
            _('File {filename} has unrecognized ({suffixes}) suffix for file type "{file_type}"').format(
                filename=filename, suffixes=", ".join(CAMT054_FILE_SUFFIXES), file_type="camt.054"
            )
        )
    with open(filename, "rb") as fp:
        data = xml_to_dict(fp.read(), array_tags=CAMT054_ARRAY_TAGS, int_tags=CAMT054_INT_TAGS)
        return data


def camt054_parse_ntfctn_acct(ntfctn: dict, default_currency: str = "EUR") -> Tuple[str, str]:
    """Returns account_number, currency from Ntfctn"""
    acc_data = ntfctn["Acct"]
    currency = acc_data.get("Ccy") or default_currency
    account_number = acc_data["Id"].get("IBAN") or acc_data["Id"].get("BBAN")
    return account_number, currency


def camt054_parse_date(data: dict, key: str) -> date:
    val = data.get(key)
    if not val or "Dt" not in val:
        raise Exception(_("Failed to parse date '{}'").format(key))
    return parse_date(val["Dt"])  # type: ignore


def camt054_parse_amt(data: dict, key: str) -> Tuple[Decimal, str]:
    val = data.get(key) or {}
    amt = val.get("Amt")
    if not amt or "@" not in amt or "@Ccy" not in amt:
        raise Exception(_("Failed to parse amount '{}'").format(key))
    return Decimal(amt["@"]), amt["@Ccy"]


def camt054_parse_rmtinf(data: dict, key: str) -> str:
    out: List[str] = []
    val = data.get(key) or {}
    ustrd_list = val.get("Ustrd")
    if ustrd_list:
        out.extend(str(ustrd).rstrip() for ustrd in ustrd_list)
    strd_list = val.get("Strd") or []
    for strd in strd_list:
        cdtrrefinf = strd.get("CdtrRefInf") or {}
        ref_val = cdtrrefinf.get("Ref")
        if ref_val:
            out.append(str(ref_val).rstrip())
        addtlrmtinf_list = strd.get("AddtlRmtInf") or []
        for addtlrmtinf in addtlrmtinf_list:
            if addtlrmtinf:
                out.append(str(addtlrmtinf).rstrip())
    return "\n".join(out)


def camt054_parse_debtor_name(data: dict, key: str) -> str:
    """
    Parses first UltmtDbtr and then Dbtr, returns first valid Nm field.
    Args:
        data: Container element
        key: Parent element name
    Returns:
        str
    """
    val = data.get(key) or {}
    if not val:
        return ""
    debtor_name = ""
    ultmtdbtr = val.get("UltmtDbtr")
    if ultmtdbtr:
        debtor_name = ultmtdbtr.get("Nm")
    if not debtor_name:
        dbtr = val.get("Dbtr")
        if dbtr:
            debtor_name = dbtr.get("Nm")
    if not debtor_name:
        raise Exception(_("Failed to parse debtor '{}'").format(key))
    return debtor_name


def camt054_parse_rltdagts_cdtragt_fininstnid_bic(data: dict, key: str) -> str:
    rltdagts = data.get(key) or {}
    cdtragt = rltdagts.get("CdtrAgt") or {}
    fininstnid = cdtragt.get("FinInstnId") or {}
    return fininstnid.get("BIC") or ""


def camt054_parse_refs_endtoendid(data: dict, key: str) -> str:
    refs = data.get(key) or {}
    return refs.get("EndToEndId") or ""


@transaction.atomic
def camt054_create_reference_payment_batch(  # pylint: disable=too-many-locals
    ntfctn: dict, name: str, file: ReferencePaymentBatchFile
) -> ReferencePaymentBatch:
    account_number, account_currency = camt054_parse_ntfctn_acct(ntfctn)
    account = Account.objects.get(name=account_number, currency=account_currency)
    assert isinstance(account, Account)
    created_datetime = parse_datetime(ntfctn["CreDtTm"])
    identifier = ntfctn["Id"]
    batch = ReferencePaymentBatch(record_date=created_datetime, file=file, identifier=identifier, name=name)
    batch.clean()
    batch.save()
    e_deposit = EntryType.objects.get(code=settings.E_BANK_DEPOSIT)
    e_withdraw = EntryType.objects.get(code=settings.E_BANK_WITHDRAW)
    for ntry in ntfctn["Ntry"]:
        rec = ReferencePaymentRecord(batch=batch, account_number=account_number, account=account)
        rec.record_date = camt054_parse_date(ntry, "BookgDt")
        rec.record_type = cdtdbtind = ntry.get("CdtDbtInd") or ""
        if cdtdbtind == "CRDT":
            rec.type = e_deposit
        elif cdtdbtind == "DBIT":
            rec.type = e_withdraw
        else:
            raise Exception(_("Unknown credit/debit indicator '{}'").format(cdtdbtind))
        rec.value_date = camt054_parse_date(ntry, "ValDt")
        ntrydtls_list = ntry.get("NtryDtls") or []
        for ntrydtls0 in ntrydtls_list:
            for txdtls in ntrydtls0["TxDtls"]:
                rec_tx = clone_model(rec, commit=False)
                assert isinstance(rec_tx, ReferencePaymentRecord)
                amtdtls = txdtls["AmtDtls"]
                if "InstdAmt" in amtdtls:
                    rec_tx.instructed_amount, rec_tx.instructed_currency = camt054_parse_amt(amtdtls, "InstdAmt")
                rec_tx.amount, rec_currency = camt054_parse_amt(amtdtls, "TxAmt")
                if rec_currency != account_currency:
                    raise Exception(_("Account currency {} does not match record currency {}").format(account_currency, rec_currency))
                rec_tx.remittance_info = camt054_parse_rmtinf(txdtls, "RmtInf")[:256]
                rec_tx.payer_name = camt054_parse_debtor_name(txdtls, "RltdPties")
                rec_tx.creditor_bank_bic = camt054_parse_rltdagts_cdtragt_fininstnid_bic(txdtls, "RltdAgts")
                rec_tx.end_to_end_identifier = camt054_parse_refs_endtoendid(txdtls, "Refs")
                rec_tx.clean()
                rec_tx.save()
    return batch
