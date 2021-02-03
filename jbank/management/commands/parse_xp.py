import logging
import os
import traceback
from django.core.management.base import CommandParser
from jutil.admin import admin_log
from jutil.format import strip_media_root
from jbank.files import list_dir_files
from jbank.helpers import process_pain002_file_content
from jbank.models import PayoutStatus
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses pain.002 payment response .XP files and updates Payout status"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("path", type=str)
        parser.add_argument("--test", action="store_true")
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--suffix", type=str, default="XP")
        parser.add_argument("--set-default-paths", action="store_true")
        parser.add_argument("--ignore-errors", action="store_true")
        parser.add_argument("--ws", type=int)

    def _set_default_paths(self, options: dict):
        default_path = os.path.abspath(options["path"])
        qs = PayoutStatus.objects.all().filter(file_path="")
        if options["ws"]:
            qs = qs.filter(payout__connection_id=options["ws"])
        objs = list(qs)
        print("Setting default path of {} status updates to {}".format(len(objs), strip_media_root(default_path)))
        for obj in objs:
            assert isinstance(obj, PayoutStatus)
            full_path = os.path.join(default_path, obj.file_name)
            if not os.path.isfile(full_path):
                msg = "Error while updating file path of PayoutStatus id={}: File {} not found".format(
                    obj.id, full_path
                )
                if not options["ignore_errors"]:
                    raise Exception(msg)
                logger.error(msg)
                continue
            file_path = strip_media_root(full_path)
            logger.info('PayoutStatus.objects.filter(id=%s).update(file_path="%s")', obj.id, file_path)
            if not options["test"]:
                PayoutStatus.objects.filter(id=obj.id).update(file_path=file_path)
                admin_log([obj], 'File path set as "{}" from terminal (parse_xp)'.format(full_path))
        print("Done")

    def do(self, *args, **options):
        if options["set_default_paths"]:
            self._set_default_paths(options)
            return

        files = list_dir_files(options["path"], "." + options["suffix"])
        for f in files:
            if PayoutStatus.objects.is_file_processed(f):
                if options["verbose"]:
                    print("Skipping processed payment status file", f)
                continue
            if options["verbose"]:
                print("Importing payment status file", f)
            try:
                with open(f, "rb") as fp:
                    process_pain002_file_content(fp.read(), f)
            except Exception:
                logger.error("Error while processing PayoutStatus id=%s: %s", f.id, traceback.format_exc())  # type: ignore
                if not options["ignore_errors"]:
                    raise
