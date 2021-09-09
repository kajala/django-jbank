import csv
import logging
from pprint import pprint
from typing import List
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from jutil.validators import variable_name_sanitizer

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Reads CSV files and generates matching parse structures
        for parsing line-based files.
        
        Default format:
        
        TITLE1,xxx
        FIELD_NAME,CHAR_COUNT
        FIELD_NAME,CHAR_COUNT
        FIELD_NAME,CHAR_COUNT
        [empty row]
        TITLE2,xxx
        FIELD_NAME,CHAR_COUNT
        FIELD_NAME,CHAR_COUNT
        FIELD_NAME,CHAR_COUNT
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--csv-file", type=str, required=True)
        parser.add_argument("--key-col-index", type=int, default=0)
        parser.add_argument("--count-col-index", type=int, default=1)
        parser.add_argument("--verbose", action="store_true")

    def do(self, *args, **kwargs):  # pylint: disable=too-many-locals,too-many-branches
        verbose = kwargs["verbose"]

        lines: List[List[str]] = []
        with open(kwargs["csv_file"], "rt", encoding="utf-8") as fp:
            reader = csv.reader(fp, dialect="excel")
            for line in reader:
                lines.append(line)
                if verbose:
                    print(line)

        key_ix = kwargs["key_col_index"]
        count_ix = kwargs["count_col_index"]
        heading = ""
        data = {}
        for line in lines:
            key = variable_name_sanitizer(line[key_ix]).lower()
            if not key:
                heading = ""
                continue
            if not heading:
                heading = key.upper()
                data[heading] = []
                continue
            n = int(line[count_ix]) if line[count_ix] else 0
            data[heading].append((key, n))

        if verbose:
            pprint(data)

        for heading, fields in data.items():
            print(f"{heading}: List[Tuple[str, str, str]] = [")
            for k, n in fields:
                print(f'    ("{k}", "X({n})", "P"),')
            print("]\n")
