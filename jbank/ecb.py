from datetime import datetime
from decimal import Decimal
import xml.etree.ElementTree as ET  # noqa
from urllib import request


def parse_euro_exchange_rates_xml(content: str):
    """
    Parses Euro currency exchange rates from string.
    Format is XML from European Central Bank (http://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml).
    Returns list of (record_date: date, currency: str, rate: str) tuples of Euro exchange rates.
    """
    out = []
    root = ET.fromstring(content)
    cube_tag = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube"
    cube = root.findall(cube_tag)[0]
    for date_cube in cube.findall(cube_tag):
        record_date = datetime.strptime(date_cube.attrib["time"], "%Y-%m-%d")
        for currency_cube in date_cube.findall(cube_tag):
            currency = currency_cube.attrib["currency"]
            out.append((record_date.date(), currency, Decimal(currency_cube.attrib["rate"])))
    return out


def download_euro_exchange_rates_xml() -> str:
    """
    Downloads Euro currency exchange rates XML file from European Central Bank.
    Returns XML as str
    """
    with request.urlopen("http://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml") as conn:
        return conn.read()
