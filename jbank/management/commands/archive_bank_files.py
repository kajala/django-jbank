import logging
import os.path
import shutil
from datetime import timedelta
from typing import Union
from django.core.management.base import CommandParser
from django.utils.timezone import now
from jutil.admin import admin_log
from jutil.format import strip_media_root
from jbank.models import StatementFile, ReferencePaymentBatchFile, PayoutStatus
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Archives old bank statements and reference payment files"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--min-age-days", type=int, default=730)

    @staticmethod
    def _archive_obj(obj: Union[StatementFile, ReferencePaymentBatchFile, PayoutStatus]):
        if obj.archived:
            logger.warning("Skipping %s since marked archived already", obj)
            return
        if not os.path.isfile(obj.full_path):
            logger.warning("Skipping %s since %s does not exist", obj, obj.full_path)
            return
        full_path = obj.full_path
        file_basename = os.path.basename(full_path)
        archived_dir = os.path.join(os.path.dirname(full_path), "archived")
        archived_full_path = os.path.join(archived_dir, file_basename)
        archived_filename = strip_media_root(archived_full_path)
        logger.info(
            "Archiving %s pk=%s: Moving %s to %s, marking as archived and storing archived_filename as %s",
            obj.__class__.__name__,
            obj.pk,
            full_path,
            archived_full_path,
            archived_filename,
        )
        if not os.path.isdir(archived_dir):
            logger.info("Directory %s does not exist so creating it", archived_dir)
            os.makedirs(archived_dir)
            logger.info("%s created", archived_dir)
        shutil.move(full_path, archived_full_path)
        obj.archived_filename = archived_filename
        obj.archived = True
        obj.save(update_fields=["archived_filename", "archived"])
        logger.info("%s archived", obj)
        admin_log([obj], f"Archived as {archived_filename}")

    def do(self, *args, **kwargs):
        old_timestamp = now() - timedelta(days=kwargs["min_age_days"])

        qs = StatementFile.objects.filter(archived=False, created__lt=old_timestamp)
        logger.info("Processing %d StatementFile objects", qs.count())
        for obj in qs.order_by("id").distinct():
            self._archive_obj(obj)

        qs = ReferencePaymentBatchFile.objects.filter(archived=False, created__lt=old_timestamp)
        logger.info("Processing %d ReferencePaymentBatchFile objects", qs.count())
        for obj in qs.order_by("id").distinct():
            self._archive_obj(obj)

        qs = PayoutStatus.objects.filter(archived=False, created__lt=old_timestamp)
        logger.info("Processing %d PayoutStatus objects", qs.count())
        for obj in qs.order_by("id").distinct():
            self._archive_obj(obj)
