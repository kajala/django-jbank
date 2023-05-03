import logging
from typing import List
import requests
from jutil.parse import parse_datetime
from jutil.format import dec4
from jutil.xml import xml_to_dict
from jbank.models import EuriborRate

logger = logging.getLogger(__name__)


def fetch_latest_euribor_rates(commit: bool = False, verbose: bool = False) -> List[EuriborRate]:
    feed_url = (
        "https://www.suomenpankki.fi/WebForms/ReportViewerPage.aspx?report=/tilastot/markkina-_ja_hallinnolliset_korot/euribor_korot_today_xml_en&output=xml"
    )
    res = requests.get(feed_url, timeout=60)
    if verbose:
        logger.info("GET %s HTTP %s\n%s", feed_url, res.status_code, res.content)
    if res.status_code >= 300:
        raise Exception(f"Failed to load Euribor rate feed from {feed_url}")
    results = xml_to_dict(res.content)
    data = results["data"]["period_Collection"]["period"]
    rates: List[EuriborRate] = []

    if isinstance(data, list):  # Sometime get a list or single item
        assert len(data) > 0
        data = data[len(data) - 1]  # Get from newest date

    record_date = parse_datetime(data["@value"]).date()
    for rate_data in data["matrix1_Title_Collection"]["rate"]:
        name = rate_data["@name"]
        rate = dec4(rate_data["intr"]["@value"])
        obj = EuriborRate(name=name, record_date=record_date, rate=rate)
        rates.append(obj)
    if commit:
        out: List[EuriborRate] = []
        for obj in rates:
            out.append(EuriborRate.objects.save_unique(obj.record_date, obj.name, obj.rate))
        return out
    return rates
