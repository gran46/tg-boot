import re
import json
import time
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def scrape_lot(url: str, source: str) -> dict | None:
    if source == "copart":
        return scrape_copart(url)
    elif source == "iaai":
        return scrape_iaai(url)
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  COPART
# ══════════════════════════════════════════════════════════════════════════════

def scrape_copart(url: str) -> dict | None:
    lot_number = extract_copart_lot(url)
    if not lot_number:
        return None
    try:
        return _selenium_scrape_copart(url, lot_number)
    except ImportError:
        logger.info("Selenium not installed")
    except Exception as e:
        logger.warning(f"Selenium/Copart failed: {e}")
    return scrape_copart_api(url, lot_number)


def extract_copart_lot(url: str) -> str | None:
    m = re.search(r"/lot/(\d+)", url)
    return m.group(1) if m else None


def _selenium_scrape_copart(url: str, lot_number: str) -> dict | None:
    driver = _make_driver()
    try:
        driver.get(f"https://www.copart.com/lot/{lot_number}")
        _wait_and_sleep(driver, "h1", 25, 6)

        page_src = driver.page_source
        soup = BeautifulSoup(page_src, "html.parser")

        # Full-res images
        images = []
        for u in re.findall(r'https?://[^\s"\'\\,\]\[]+_ful\.\w+', page_src):
            if u not in images:
                images.append(u)
        if len(images) < 3:
            for u in re.findall(r'https://cs\.copart\.com/v1/AUTH_[^\s"\'\\,\]\[]+\.(?:jpg|jpeg|png)', page_src, re.I):
                if "_thb" not in u and "_thmb" not in u and u not in images:
                    images.append(u)
        if len(images) < 3:
            for u in re.findall(r'https://cs\.copart\.com[^\s"\'\\,\]\[]+\.(?:jpg|jpeg|png)', page_src, re.I):
                full = re.sub(r'_th[bm]\.', '_ful.', u)
                if full not in images:
                    images.append(full)

        details = _extract_details(soup, page_src)
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else "Невідомо"

        def d(*keys):
            return _find(details, *keys)

        # Engine: look for displacement like 2.0L
        engine = d("engine", "displacement")
        if not re.search(r'\d+\.\d+\s*[Ll]', engine or ""):
            m = re.search(r'(\d+\.\d+\s*[Ll](?:\s+(?:DOHC|SOHC|V\d|I\d|Turbo|[A-Z0-9]{1,10})){0,3})', page_src)
            engine = m.group(1).strip() if m else "—"

        drive = _clean_drive(d("drive", "drivetrain"))

        # Condition: Run & Drive / Starts / Stationary
        condition = d("highlights", "condition", "run")
        if condition == "—":
            page_low = page_src.lower()
            if "run and drive" in page_low or "run & drive" in page_low:
                condition = "Run and Drive"
            elif "starts and drives" in page_low or "starts & drives" in page_low:
                condition = "Starts and Drives"
            elif "starts" in page_low and "drive" not in page_low:
                condition = "Starts"
            elif "stationary" in page_low or "does not start" in page_low:
                condition = "Stationary"

        return {
            "url": url, "title": title,
            "current_bid": d("current bid", "high bid"),
            "primary_damage": d("primary damage"),
            "secondary_damage": d("secondary damage"),
            "engine": engine,
            "cylinders": d("cylinders", "cylinder"),
            "transmission": d("transmission"),
            "drive": drive,
            "fuel": d("fuel"),
            "color": d("color", "colour"),
            "body_style": d("body style", "body"),
            "odometer": d("odometer", "mileage"),
            "keys": d("key"),
            "condition": condition,
            "sale_date": d("sale date"),
            "images": images[:20],
        }
    finally:
        driver.quit()


# ══════════════════════════════════════════════════════════════════════════════
#  IAAI
# ══════════════════════════════════════════════════════════════════════════════

def scrape_iaai(url: str) -> dict | None:
    # Extract stock number from URL
    stock = extract_iaai_stock(url)

    try:
        return _selenium_scrape_iaai(url, stock)
    except ImportError:
        logger.info("Selenium not installed for IAAI")
    except Exception as e:
        logger.warning(f"Selenium/IAAI failed: {e}")
    return None


def extract_iaai_stock(url: str) -> str | None:
    m = re.search(r'/(\d{7,})', url)
    return m.group(1) if m else None


def _selenium_scrape_iaai(url: str, stock: str | None) -> dict | None:
    driver = _make_driver()
    try:
        driver.get(url)
        _wait_and_sleep(driver, "h1", 25, 7)

        page_src = driver.page_source
        soup = BeautifulSoup(page_src, "html.parser")

        # ── IAAI IMAGES ───────────────────────────────────────────────────────
        # IAAI uses vis.iaai.com/retriever for full-res images
        images = []

        # Method 1: find retriever URLs directly (full-res)
        retriever_urls = re.findall(
            r'https://vis\.iaai\.com/retriever\?imageKeys=[^\s"\'\\&]+(?:&width=\d+&height=\d+)?',
            page_src
        )
        for u in retriever_urls:
            # Force max resolution
            u_clean = re.sub(r'&width=\d+&height=\d+', '', u)
            # Build full-res URL
            m = re.search(r'imageKeys=([^&\s"\'\\]+)', u_clean)
            if m:
                keys = m.group(1)
                # Extract dimensions from key pattern ~RW(\d+)~H(\d+)
                rw = re.search(r'~RW(\d+)', keys)
                h = re.search(r'~H(\d+)', keys)
                if rw and h:
                    full_url = f"https://vis.iaai.com/retriever?imageKeys={keys}&width={rw.group(1)}&height={h.group(1)}"
                else:
                    full_url = f"https://vis.iaai.com/retriever?imageKeys={keys}&width=2576&height=1932"
                if full_url not in images:
                    images.append(full_url)

        # Method 2: find imageKeys patterns and build retriever URLs
        if len(images) < 3 and stock:
            img_keys = re.findall(
                rf'{stock}~SID~[A-Z0-9]+~S\d+~I(\d+)~RW(\d+)~H(\d+)',
                page_src
            )
            prefix_m = re.search(rf'({stock}~SID~[A-Z0-9]+)', page_src)
            if prefix_m:
                prefix = prefix_m.group(1)
                seen_idx = set()
                for idx, rw, h in img_keys:
                    if idx not in seen_idx:
                        seen_idx.add(idx)
                        key = f"{prefix}~S0~I{idx}~RW{rw}~H{h}~TH0"
                        u = f"https://vis.iaai.com/retriever?imageKeys={key}&width={rw}&height={h}"
                        if u not in images:
                            images.append(u)

        # Method 3: generic vis.iaai.com URLs
        if len(images) < 3:
            all_vis = re.findall(r'https://vis\.iaai\.com/[^\s"\'\\,\]\[]+', page_src)
            for u in all_vis:
                if "retriever" in u and u not in images:
                    images.append(u)

        # ── IAAI DETAILS ──────────────────────────────────────────────────────
        details = _extract_details(soup, page_src)

        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else "Невідомо"

        def d(*keys):
            return _find(details, *keys)

        engine = d("engine", "displacement", "engine type")
        if not re.search(r'\d+\.\d+\s*[Ll]', engine or ""):
            m = re.search(r'(\d+\.\d+\s*[Ll](?:\s+\S+){0,3})', page_src)
            engine = m.group(1).strip() if m else "—"

        drive = _clean_drive(d("drive", "drivetrain", "drive type"))

        # Keys for IAAI: often "Present" / "Absent"
        keys_val = d("key")
        if keys_val not in ("—", None, ""):
            low = keys_val.lower()
            if "present" in low or "yes" in low:
                keys_val = "Yes"
            elif "absent" in low or "no" in low or "none" in low:
                keys_val = "No"

        # Odometer: clean up IAAI format
        odo = d("odometer", "mileage", "miles")
        if odo and odo != "—":
            # Remove "Actual" / "Not Actual" suffix junk
            odo = re.sub(r'\s*(Actual|Not Actual|Exempt|mi|Miles?).*', '', odo, flags=re.I).strip()
            odo_num = re.search(r'[\d,]+', odo)
            if odo_num:
                odo = odo_num.group(0) + " mi"

        # Sale date: IAAI often has "Lane/Run #: ..." — skip that
        sale = d("sale date", "auction date")
        if sale and ("Lane" in sale or "Run" in sale or "#" in sale):
            sale = "—"

        # Condition for IAAI
        condition_iaai = d("condition", "highlights", "engine starts")
        if condition_iaai == "—":
            page_low = page_src.lower()
            if "run and drive" in page_low or "run & drive" in page_low:
                condition_iaai = "Run and Drive"
            elif "starts and drives" in page_low:
                condition_iaai = "Starts and Drives"
            elif "engine start" in page_low:
                condition_iaai = "Starts"
            elif "stationary" in page_low or "does not start" in page_low:
                condition_iaai = "Stationary"

        return {
            "url": url, "title": title,
            "current_bid": d("current bid", "high bid", "bid"),
            "primary_damage": d("primary damage", "loss type", "damage"),
            "secondary_damage": d("secondary damage"),
            "engine": engine,
            "cylinders": d("cylinders", "cylinder count"),
            "transmission": d("transmission"),
            "drive": drive,
            "fuel": d("fuel", "fuel type"),
            "color": d("color", "colour"),
            "body_style": d("body style", "body type", "style"),
            "odometer": odo,
            "keys": keys_val,
            "condition": condition_iaai,
            "sale_date": sale,
            "images": images[:20],
        }
    finally:
        driver.quit()


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(options=opts)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def _wait_and_sleep(driver, css_selector: str, timeout: int, sleep: float):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
    )
    time.sleep(sleep)


def _extract_details(soup: BeautifulSoup, page_src: str) -> dict:
    details = {}

    # <tr> rows
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) == 2:
            k = cells[0].get_text(strip=True).lower()
            v = cells[1].get_text(strip=True)
            if k and v and len(v) < 150 and not _is_garbage(v):
                details[k] = v

    # dl/dt/dd
    for dl in soup.select("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            k = dt.get_text(strip=True).lower()
            v = dd.get_text(strip=True)
            if k and v and len(v) < 150 and not _is_garbage(v):
                details[k] = v

    # span pairs inside detail blocks
    for block in soup.select(
        "[class*='lot-detail'],[class*='LotDetail'],[class*='detail'],"
        "[class*='vehicle-info'],[class*='VehicleInfo'],[class*='specs']"
    ):
        spans = block.find_all("span")
        for i in range(0, len(spans) - 1, 2):
            k = spans[i].get_text(strip=True).lower()
            v = spans[i+1].get_text(strip=True)
            if k and v and len(k) < 60 and len(v) < 150 and not _is_garbage(v):
                details[k] = v

    # Regex fallback on page text
    page_text = soup.get_text(separator="\n")
    patterns = {
        "odometer":         r'Odometer[:\s]+([\d,]+\s*(?:mi|km))',
        "transmission":     r'Transmission[:\s]+([^\n]{3,40})',
        "fuel":             r'Fuel(?:\s+Type)?[:\s]+([^\n]{3,25})',
        "keys":             r'(?:Has Keys?|Keys?)[:\s]+(Yes|No|Present|Absent)',
        "color":            r'Color[:\s]+([^\n]{3,25})',
        "cylinders":        r'Cylinders?[:\s]+(\d+)',
        "body style":       r'Body(?:\s+Style)?[:\s]+([^\n]{3,30})',
        "drive":            r'(?:Drive|Drivetrain)(?:\s+Type)?[:\s]+((?:AWD|FWD|RWD|4WD|4x4|All.Wheel|Front.Wheel|Rear.Wheel)[^\n]{0,20})',
        "sale date":        r'Sale Date[:\s]+([^\n]{3,30})',
        "primary damage":   r'Primary Damage[:\s]+([^\n]{3,50})',
        "secondary damage": r'Secondary Damage[:\s]+([^\n]{3,50})',
        "current bid":      r'Current Bid[:\s]+\$?([\d,]+)',
        "loss type":        r'Loss Type[:\s]+([^\n]{3,50})',
    }
    for field, pat in patterns.items():
        if field not in details:
            m = re.search(pat, page_text, re.I)
            if m:
                val = m.group(1).strip()
                if not _is_garbage(val):
                    details[field] = val

    return details


def _find(details: dict, *keys) -> str:
    for k in keys:
        kl = k.lower()
        if kl in details:
            return details[kl]
        for dk, dv in details.items():
            if kl in dk:
                return dv
    return "—"


def _clean_drive(val: str) -> str:
    if not val or val == "—":
        return "—"
    m = re.search(r'(AWD|FWD|RWD|4WD|4x4)', val, re.I)
    if m:
        return m.group(1).upper()
    low = val.lower()
    if "all" in low:
        return "AWD"
    if "front" in low:
        return "FWD"
    if "rear" in low:
        return "RWD"
    if "4" in low:
        return "4WD"
    return val


def _is_garbage(v: str) -> bool:
    bad = ["Check now", "Pure sale", "Bid now", "Haven't bid", "Location:"]
    return any(b in v for b in bad) or ("|" in v and len(v) > 30)


# ══════════════════════════════════════════════════════════════════════════════
#  COPART API FALLBACK
# ══════════════════════════════════════════════════════════════════════════════

def scrape_copart_api(url: str, lot_number: str) -> dict | None:
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get(f"https://www.copart.com/lot/{lot_number}", timeout=15)
        time.sleep(1)
    except Exception:
        pass

    lot = {}
    images = []
    for ep in [
        f"https://www.copart.com/public/data/lotdetails/solr/{lot_number}/USA",
        f"https://www.copart.com/public/data/lotdetails/solr/{lot_number}/CAN",
    ]:
        try:
            r = session.get(ep, timeout=15)
            if r.status_code == 200:
                d = r.json().get("data", {})
                if "lotDetails" in d:
                    lot = d["lotDetails"]
                    break
        except Exception:
            pass

    try:
        r = session.get(f"https://www.copart.com/public/data/lotdetails/solr/lotImages/{lot_number}/USA", timeout=15)
        if r.status_code == 200:
            img_data = r.json().get("data", {}).get("imagesList", {})
            for key in ("FULL_IMAGE", "HIGH_RESOLUTION_IMAGE", "THUMBNAIL_IMAGE"):
                items = img_data.get(key, [])
                if items:
                    images = [i["url"] for i in items if i.get("url")]
                    break
    except Exception:
        pass

    def g(k):
        v = lot.get(k)
        return str(v) if v not in (None, "", "null", 0) else "—"

    parts = [g("lcy"), g("mkn") or g("mke"), g("mdl"), g("ld")]
    title = " ".join(p for p in parts if p != "—") or "Невідомо"

    return {
        "url": url, "title": title,
        "current_bid": f'${g("hb")}' if g("hb") != "—" else "—",
        "primary_damage": g("dd"), "secondary_damage": g("sd"),
        "engine": g("egn"), "cylinders": g("cyl"),
        "transmission": g("tsmn"), "drive": g("drv"),
        "fuel": g("ft"), "color": g("clr"),
        "body_style": g("bstl"),
        "odometer": f'{g("orr")} {g("ord")}',
        "keys": g("hk"), "sale_date": g("ad"),
        "images": images[:20],
    }
