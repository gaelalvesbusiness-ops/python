"""Step 2 diagnostic: inspect honestpool.ru puzzle listings with a visible browser.

Opens the original listing and the verified-only listing, waits for manual
verification if a CAPTCHA is shown (it is never solved or bypassed here),
then records table structure, status badges, pagination links, hex value
completeness and network traffic.

Cookies, session tokens, CSRF values and auth headers are redacted from every
saved file, and the outputs are audited for leftovers before the script exits.

Usage:
    python inspect_site.py [--manual-timeout 300] [--no-next-page] [--channel chrome]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Response, sync_playwright

from scraper.config import DATA_DIR, DEFAULT_BASE_URL, LOGS_DIR

CHECKED_URL = "https://honestpool.ru/puzzlePoolChecked?id=71"
STATUS_TEXT = "Проверено"

REPORT_PATH = LOGS_DIR / "site_inspection.txt"
DETAILS_PATH = LOGS_DIR / "site_inspection.json"

HEX_RE = re.compile(r"^(0x)?[0-9a-fA-F]{6,}$")
MASK_RE = re.compile(r"(\.\.\.|…|\*{2,}|•{2,})")
HEX_IN_TEXT_RE = re.compile(r"(?:0x)?[0-9a-fA-F]{16,}")

REDACTED = "REDACTED"
# "key" alone is deliberately absent: puzzle data may use keys such as "keyStart".
SENSITIVE_NAME = (
    r"(?:token|csrf|xsrf|session|sessid|auth(?!ors?\b|ority)|password|passwd|secret|api_?key|signature|bearer)"
)
# CAPTCHA answers are only redacted as name=value pairs, so page titles, URLs and JSON keys
# such as "captchaProbe" that merely mention "captcha" stay readable.
FORM_FIELD_NAME = rf"[\w.\-\[\]]*(?:{SENSITIVE_NAME}|captcha)[\w.\-\[\]]*"
REDACTION_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?im)^(\s*(?:set-cookie|cookie|authorization|proxy-authorization)\s*:\s*).+$"),
     rf"\g<1>{REDACTED}"),
    (re.compile(r'(?i)("(?:set-cookie|cookie|authorization|proxy-authorization)"\s*:\s*)"(?:[^"\\]|\\.)*"'),
     rf'\g<1>"{REDACTED}"'),
    (re.compile(rf'(?i)("[^"]*{SENSITIVE_NAME}[^"]*"\s*:\s*)"(?:[^"\\]|\\.)*"'),
     rf'\g<1>"{REDACTED}"'),
    # Form bodies and query strings; the first field of a POST body has no leading ?, & or ;.
    (re.compile(rf"(?i)(?<![^?&;\s\"'])({FORM_FIELD_NAME}=)[^&\s\"'<>\\]+"),
     rf"\g<1>{REDACTED}"),
    (re.compile(rf"(?i)(\b\w*{SENSITIVE_NAME}\w*\s*[:=]\s*)(['\"])[^'\"]+\2"),
     rf"\g<1>\g<2>{REDACTED}\g<2>"),
]
SENSITIVE_TAG_RE = re.compile(r"<(?:meta|input)\b[^>]*>", re.I)
# Quotes may be backslash-escaped when HTML is embedded in a JSON string.
SENSITIVE_TAG_NAME_RE = re.compile(
    rf"""(?:name|id|property)\s*=\s*\\?["'][^"']*(?:{SENSITIVE_NAME}|captcha)""", re.I
)
TAG_VALUE_RE = re.compile(r"""((?:value|content)\s*=\s*)(\\?["'])[^"']*?\2""", re.I)
AUDIT_RE = re.compile(
    rf"(?i)(set-cookie|cookie\s*:|authorization\s*:|phpsessid|laravel_session|sessionid|"
    rf"\bsid=|\bcaptcha=|{SENSITIVE_NAME})[^\n]{{0,80}}"
)


def redact(text: str) -> str:
    """Strip cookies, session/CSRF tokens and auth values; hex puzzle data is left untouched."""
    text = SENSITIVE_TAG_RE.sub(
        lambda m: TAG_VALUE_RE.sub(rf"\g<1>\g<2>{REDACTED}\g<2>", m.group(0))
        if SENSITIVE_TAG_NAME_RE.search(m.group(0)) else m.group(0),
        text,
    )
    for pattern, replacement in REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text


def write_redacted(path: Path, text: str) -> None:
    path.write_text(redact(text), encoding="utf-8")


def audit_outputs(paths: list[Path]) -> list[str]:
    """Return file:line locations that still look like credentials; the values themselves are never included."""
    findings = []
    for path in paths:
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in AUDIT_RE.finditer(line):
                if REDACTED not in m.group(0):
                    findings.append(f"{path.name}:{lineno}: unredacted value near '{m.group(1)}'")
    return findings

JS_HELPERS = r"""
const cssPath = (el) => {
  const parts = [];
  while (el && el.nodeType === 1 && el !== document.documentElement && parts.length < 8) {
    let p = el.tagName.toLowerCase();
    if (el.id) { parts.unshift(p + '#' + CSS.escape(el.id)); break; }
    p += [...el.classList].slice(0, 3).map(c => '.' + CSS.escape(c)).join('');
    const parent = el.parentElement;
    if (parent) {
      const same = [...parent.children].filter(c => c.tagName === el.tagName);
      if (same.length > 1) p += `:nth-of-type(${same.indexOf(el) + 1})`;
    }
    parts.unshift(p);
    el = parent;
  }
  return parts.join(' > ');
};
const styleOf = (el) => {
  const s = getComputedStyle(el);
  return { color: s.color, backgroundColor: s.backgroundColor };
};
const attrsOf = (el) => Object.fromEntries([...el.attributes].map(a => [a.name, a.value]));
const ownText = (el) => [...el.childNodes].filter(n => n.nodeType === 3)
  .map(n => n.textContent).join('').trim();
"""

PROBE_JS = (
    "() => {"
    + JS_HELPERS
    + r"""
  const body = document.body ? document.body.innerText : '';
  const indicators = [];
  for (const f of document.querySelectorAll('iframe')) {
    indicators.push({ kind: 'iframe', src: f.src, title: f.title });
  }
  for (const s of document.querySelectorAll('script[src]')) {
    if (/captcha|turnstile|challenge|ddos|recaptcha|hcaptcha|smartcaptcha|antibot/i.test(s.src)) {
      indicators.push({ kind: 'script', src: s.src });
    }
  }
  const sel = '[class*=captcha i],[id*=captcha i],.g-recaptcha,.h-captcha,.cf-turnstile,' +
              '.smart-captcha,[data-sitekey],#challenge-form,#challenge-stage';
  for (const el of document.querySelectorAll(sel)) {
    indicators.push({ kind: 'element', selector: cssPath(el), attrs: attrsOf(el) });
  }
  const keywords = ['captcha', 'капч', 'не робот', 'not a robot', 'just a moment',
                    'checking your browser', 'ddos-guard', 'ddos guard', 'cloudflare',
                    'verify you are human', 'access denied', 'доступ запрещ'];
  const lower = (document.title + '\n' + body).toLowerCase();
  const keywordHits = keywords.filter(k => lower.includes(k));
  const rows = document.querySelectorAll('table tbody tr').length;
  return {
    url: location.href, title: document.title, readyState: document.readyState,
    bodySnippet: body.slice(0, 1500), indicators, keywordHits,
    tableRowCount: rows, hasStatusText: body.includes('Проверено'),
  };
}"""
)

ANALYZE_JS = (
    "() => {"
    + JS_HELPERS
    + r"""
  const tables = [...document.querySelectorAll('table')].map((t, ti) => {
    let headers = [...t.querySelectorAll('thead th, thead td')].map(c => c.innerText.trim());
    const bodyRows = [...t.querySelectorAll('tbody tr')];
    if (!headers.length) {
      const first = t.querySelector('tr');
      if (first) headers = [...first.children].map(c => c.innerText.trim());
    }
    const statusCol = headers.findIndex(h => /статус|status/i.test(h));
    const statusVariants = {};
    if (statusCol >= 0) {
      for (const r of bodyRows) {
        const cell = r.children[statusCol];
        if (!cell) continue;
        let leaf = cell;
        while (leaf.children.length === 1) leaf = leaf.children[0];
        const text = cell.innerText.trim();
        const key = text + ' | ' + leaf.tagName + '.' + [...leaf.classList].join('.');
        if (!statusVariants[key]) {
          statusVariants[key] = {
            text, count: 0, leafTag: leaf.tagName.toLowerCase(), leafClass: leaf.className,
            leafSelector: cssPath(leaf), leafStyle: styleOf(leaf), cellStyle: styleOf(cell),
            cellHTML: cell.innerHTML.trim().slice(0, 500),
          };
        }
        statusVariants[key].count++;
      }
    }
    return {
      index: ti, selector: cssPath(t), id: t.id, className: t.className,
      headers, statusColumnIndex: statusCol,
      bodyRowCount: bodyRows.length, totalRowCount: t.querySelectorAll('tr').length,
      statusVariants: Object.values(statusVariants),
      sampleRows: bodyRows.slice(0, 3).map(r => ({
        selector: cssPath(r), attrs: attrsOf(r),
        cells: [...r.children].map(c => ({
          tag: c.tagName.toLowerCase(), attrs: attrsOf(c),
          innerText: c.innerText.trim(), textContent: c.textContent.trim(),
          html: c.innerHTML.trim().slice(0, 800),
        })),
      })),
      allRows: bodyRows.map(r => [...r.children].map(c => c.innerText.trim())),
      allRowsTextContent: bodyRows.map(r => [...r.children].map(c => c.textContent.trim())),
    };
  });

  const exactStatus = [];
  let exactStatusCount = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (ownText(el) === 'Проверено') {
      exactStatusCount++;
      if (exactStatus.length < 5) {
        exactStatus.push({
          tag: el.tagName.toLowerCase(), className: el.className, selector: cssPath(el),
          attrs: attrsOf(el), style: styleOf(el), outerHTML: el.outerHTML.slice(0, 400),
          parentHTML: el.parentElement ? el.parentElement.outerHTML.slice(0, 600) : null,
        });
      }
    }
  }

  const here = new URL(location.href);
  const links = [...document.querySelectorAll('a[href]')].map(a => ({
    text: a.innerText.trim(), href: a.href, rawHref: a.getAttribute('href'),
    className: a.className, selector: cssPath(a),
    parentClass: a.parentElement ? a.parentElement.className : '',
  }));
  const navWords = /^(\d+|»|«|›|‹|>|<|>>|<<|…|\.\.\.)$|след|пред|перв|послед|next|prev|first|last/i;
  const paginationLinks = links.filter(l => {
    let samePath = false;
    try {
      const u = new URL(l.href);
      samePath = u.origin === here.origin && u.pathname === here.pathname && u.search !== here.search;
    } catch (e) {}
    return samePath || navWords.test(l.text);
  });
  const paginationContainers = [...document.querySelectorAll(
    '[class*=pag i], nav, [aria-label*=pag i], [role=navigation]')]
    .map(e => ({ selector: cssPath(e), className: e.className, html: e.outerHTML.slice(0, 4000) }));
  const forms = [...document.querySelectorAll('form')].map(f => ({
    action: f.action, method: f.method, selector: cssPath(f), html: f.outerHTML.slice(0, 2000),
  }));
  const pageInputs = [...document.querySelectorAll('input, select')].map(i => ({
    tag: i.tagName.toLowerCase(), selector: cssPath(i), attrs: attrsOf(i),
  }));

  return {
    url: location.href, title: document.title, tables, exactStatus, exactStatusCount,
    paginationLinks, paginationContainers, forms, pageInputs, linkCount: links.length,
  };
}"""
)

MARK_NEXT_JS = r"""() => {
  const here = new URL(location.href);
  const cands = [...document.querySelectorAll('a[href]')].filter(a => {
    try {
      const u = new URL(a.href);
      return u.origin === here.origin && u.pathname === here.pathname && u.search !== here.search;
    } catch (e) { return false; }
  });
  let pick = cands.find(a => a.innerText.trim() === '2')
          || cands.find(a => /след|next|»|›/i.test(a.innerText.trim()));
  if (!pick) {
    const any = [...document.querySelectorAll('a, button')];
    pick = any.find(a => a.innerText.trim() === '2')
        || any.find(a => /^(след|next|»|›|>)/i.test(a.innerText.trim()));
  }
  if (!pick) return null;
  pick.setAttribute('data-inspect-next', '1');
  return { text: pick.innerText.trim(), href: pick.getAttribute('href'), tag: pick.tagName.toLowerCase() };
}"""


class NetworkRecorder:
    """Collects responses for the current phase; bodies are read before the next navigation."""

    def __init__(self, page: Page) -> None:
        self.phase = "init"
        self.entries: list[dict[str, Any]] = []
        self._pending: list[tuple[dict[str, Any], Response]] = []
        page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        req = response.request
        entry = {
            "phase": self.phase,
            "method": req.method,
            "url": response.url,
            "status": response.status,
            "resourceType": req.resource_type,
            "contentType": response.headers.get("content-type", ""),
            "server": response.headers.get("server", ""),
            "postData": redact((req.post_data or "")[:1000]) if req.method != "GET" else "",
        }
        self.entries.append(entry)
        if req.resource_type in ("document", "xhr", "fetch"):
            self._pending.append((entry, response))

    def start(self, phase: str) -> None:
        self.flush_bodies()
        self.phase = phase

    def flush_bodies(self) -> None:
        for entry, response in self._pending:
            try:
                text = response.text()
            except Exception as exc:  # noqa: BLE001 - body may be unavailable (redirects, evicted)
                entry["bodyError"] = str(exc)[:200]
                continue
            entry["bodyLength"] = len(text)
            entry["bodyHead"] = text[:3000]
            entry["_body"] = text
        self._pending.clear()

    def bodies(self, phase: str) -> list[dict[str, Any]]:
        return [e for e in self.entries if e["phase"] == phase and "_body" in e]


def safe_evaluate(page: Page, script: str) -> Any:
    for _ in range(10):
        try:
            return page.evaluate(script)
        except PlaywrightError as exc:
            if "context was destroyed" in str(exc) or "navigation" in str(exc).lower():
                page.wait_for_timeout(1000)
                continue
            raise
    return page.evaluate(script)


def captcha_suspected(probe: dict[str, Any]) -> bool:
    return bool(probe["indicators"] or probe["keywordHits"])


def wait_for_access(
    page: Page, label: str, manual_timeout: int, shot_path: Path
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    """Return (accessible, final_probe, first_captcha_probe). Never interacts with a CAPTCHA."""
    first_probe = safe_evaluate(page, PROBE_JS)
    captcha_probe = None
    if captcha_suspected(first_probe) or first_probe["tableRowCount"] == 0:
        captcha_probe = first_probe
        page.screenshot(path=str(shot_path), full_page=True)
        print(
            f"\n[{label}] Table not visible yet. Possible challenge indicators: "
            f"{first_probe['indicators'] or 'none'}; keywords: {first_probe['keywordHits'] or 'none'}"
        )
        print(
            f"[{label}] If a CAPTCHA is shown, please complete it manually in the browser window. "
            f"Waiting up to {manual_timeout}s..."
        )
    deadline = time.monotonic() + manual_timeout
    probe = first_probe
    while True:
        if probe["tableRowCount"] > 0:
            return True, probe, captcha_probe
        if time.monotonic() > deadline:
            return False, probe, captcha_probe
        page.wait_for_timeout(2000)
        probe = safe_evaluate(page, PROBE_JS)


def analyze_hex(table: dict[str, Any], raw_bodies: list[str]) -> list[dict[str, Any]]:
    results = []
    rows = table["allRows"]
    rows_tc = table["allRowsTextContent"]
    for col, header in enumerate(table["headers"]):
        values = [r[col] for r in rows if col < len(r)]
        values_tc = [r[col] for r in rows_tc if col < len(r)]
        hexish = [v for v in values if HEX_RE.match(v.replace(" ", ""))]
        masked = [v for v in values if MASK_RE.search(v)]
        if not hexish and not masked:
            continue
        lengths = sorted({len(v) for v in hexish})
        in_raw = sum(1 for v in hexish if any(v in b for b in raw_bodies))
        longer_in_raw = []
        for v in masked[:3]:
            prefix = MASK_RE.split(v)[0].strip()
            if len(prefix) >= 4:
                for b in raw_bodies:
                    for m in HEX_IN_TEXT_RE.finditer(b):
                        if m.group(0).lower().startswith(prefix.lower()) and len(m.group(0)) > len(prefix):
                            longer_in_raw.append(m.group(0))
                            break
        results.append({
            "column": col, "header": header, "values": len(values),
            "hexLike": len(hexish), "masked": len(masked), "hexLengths": lengths,
            "innerTextDiffersFromTextContent": sum(1 for a, b in zip(values, values_tc) if a != b),
            "hexValuesFoundInRawResponses": in_raw,
            "samples": values[:3], "samplesTextContent": values_tc[:3],
            "longerHexInRawForMasked": longer_in_raw[:3],
        })
    return results


def summarize_params(links: list[dict[str, Any]]) -> dict[str, list[str]]:
    params: dict[str, set[str]] = {}
    for link in links:
        for key, value in parse_qsl(urlparse(link["href"]).query, keep_blank_values=True):
            params.setdefault(key, set()).add(value)
    return {k: sorted(v, key=lambda s: (len(s), s))[:15] for k, v in params.items()}


def inspect_listing(
    page: Page,
    net: NetworkRecorder,
    label: str,
    url: str,
    html_path: Path,
    png_path: Path,
    manual_timeout: int,
    click_next: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {"label": label, "requestedUrl": url}
    phase = f"{label}:load"
    net.start(phase)
    print(f"\n[{label}] Opening {url}")
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightError as exc:
        result["error"] = f"navigation failed: {exc}"
        return result
    if response is not None:
        result["documentStatus"] = response.status
        result["documentHeaders"] = {
            k: v for k, v in response.headers.items()
            if k in ("server", "content-type", "cf-ray", "x-powered-by",
                     "cache-control", "x-frame-options", "content-security-policy")
        }
        result["documentSetsCookies"] = "set-cookie" in response.headers
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightError:
        result["networkIdleTimeout"] = True

    captcha_png = LOGS_DIR / f"captcha_{label}.png"
    accessible, probe, captcha_probe = wait_for_access(page, label, manual_timeout, captcha_png)
    result["accessible"] = accessible
    result["finalProbe"] = probe
    result["captchaProbe"] = captcha_probe
    if captcha_probe is not None:
        result["captchaScreenshot"] = str(captcha_png)
    if not accessible:
        page.screenshot(path=str(png_path), full_page=True)
        write_redacted(html_path, page.content())
        return result

    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass
    analysis = safe_evaluate(page, ANALYZE_JS)
    write_redacted(html_path, page.content())
    page.screenshot(path=str(png_path), full_page=True)
    net.flush_bodies()
    raw_bodies = [e["_body"] for e in net.bodies(phase)]

    result["analysis"] = analysis
    result["paginationParams"] = summarize_params(analysis["paginationLinks"])
    result["hex"] = [
        {"table": t["index"], "columns": analyze_hex(t, raw_bodies)} for t in analysis["tables"]
    ]
    result["xhrDuringLoad"] = [
        {k: v for k, v in e.items() if k != "_body"}
        for e in net.entries
        if e["phase"] == phase and e["resourceType"] in ("xhr", "fetch")
    ]

    if click_next:
        time.sleep(3)
        marked = safe_evaluate(page, MARK_NEXT_JS)
        result["nextControl"] = marked
        if marked:
            next_phase = f"{label}:next"
            net.start(next_phase)
            url_before = page.url
            page.click("[data-inspect-next]")
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PlaywrightError:
                pass
            page.wait_for_timeout(1500)
            after = safe_evaluate(page, PROBE_JS)
            after_analysis = safe_evaluate(page, ANALYZE_JS) if after["tableRowCount"] else None
            net.flush_bodies()
            result["nextPage"] = {
                "urlBefore": url_before,
                "urlAfter": page.url,
                "probe": after,
                "firstRows": (after_analysis["tables"][0]["allRows"][:3]
                              if after_analysis and after_analysis["tables"] else None),
                "requests": [
                    {k: v for k, v in e.items() if k not in ("_body", "bodyHead")}
                    for e in net.entries
                    if e["phase"] == next_phase and e["resourceType"] in ("document", "xhr", "fetch")
                ],
            }
    return result


def fmt(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def build_report(results: list[dict[str, Any]], net: NetworkRecorder, started: str) -> str:
    out: list[str] = [
        "HonestPool site inspection report",
        f"Started: {started}",
        f"Finished: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    for r in results:
        out += ["=" * 78, f"LISTING: {r['label']}  ({r['requestedUrl']})", "=" * 78]
        if "error" in r:
            out += [f"ERROR: {r['error']}", ""]
            continue
        out += [
            f"Document HTTP status: {r.get('documentStatus')}",
            f"Document headers: {fmt(r.get('documentHeaders'))}",
            f"Accessible (table rows rendered): {r['accessible']}",
        ]
        cp = r.get("captchaProbe")
        if cp:
            out += [
                "Challenge / no-table state observed on first load:",
                f"  title: {cp['title']}",
                f"  url: {cp['url']}",
                f"  indicators: {fmt(cp['indicators'])}",
                f"  keyword hits: {cp['keywordHits']}",
                f"  body snippet: {cp['bodySnippet'][:600]!r}",
                f"  screenshot: {r.get('captchaScreenshot')}",
            ]
        else:
            out.append("No CAPTCHA indicators and table rendered on first load.")
        fp = r["finalProbe"]
        out += [f"Final URL: {fp['url']}", f"Final title: {fp['title']}", ""]
        if not r["accessible"]:
            out += ["Page did not become accessible within the manual timeout.", ""]
            continue

        a = r["analysis"]
        out.append(f"Tables found: {len(a['tables'])}")
        for t in a["tables"]:
            out += [
                f"-- table[{t['index']}] selector: {t['selector']}",
                f"   id={t['id']!r} class={t['className']!r}",
                f"   headers: {t['headers']}",
                f"   tbody rows: {t['bodyRowCount']} (all tr: {t['totalRowCount']})",
                f"   status column index: {t['statusColumnIndex']}",
                f"   status variants: {fmt(t['statusVariants'])}",
                f"   first rows (innerText): {fmt(t['allRows'][:3])}",
                f"   sample row cell detail: {fmt(t['sampleRows'][:1])}",
            ]
        out += [
            "",
            f"Elements whose own text is exactly '{STATUS_TEXT}': {a['exactStatusCount']}",
            f"Examples: {fmt(a['exactStatus'][:2])}",
            "",
            "Hex column analysis:",
            fmt(r["hex"]),
            "",
            f"Pagination-like links ({len(a['paginationLinks'])}):",
            fmt([{k: l[k] for k in ('text', 'rawHref', 'className', 'parentClass')}
                 for l in a["paginationLinks"][:40]]),
            f"Query parameters seen in pagination links: {fmt(r['paginationParams'])}",
            f"Pagination containers: {fmt([{k: c[k] for k in ('selector', 'className')} for c in a['paginationContainers']])}",
            f"First pagination container HTML: {a['paginationContainers'][0]['html'][:2500] if a['paginationContainers'] else None}",
            f"Forms: {fmt(a['forms'])}",
            f"Inputs/selects: {fmt(a['pageInputs'][:15])}",
            "",
            f"XHR/fetch during initial load: {fmt(r['xhrDuringLoad'])}",
            "",
            f"Next-page control clicked: {fmt(r.get('nextControl'))}",
            f"Next-page result: {fmt(r.get('nextPage'))}",
            "",
        ]

    out += ["=" * 78, "ALL NETWORK RESPONSES (excluding images/fonts/stylesheets)", "=" * 78]
    for e in net.entries:
        if e["resourceType"] in ("image", "font", "stylesheet", "media"):
            continue
        out.append(
            f"[{e['phase']}] {e['method']} {e['status']} {e['resourceType']:<9} "
            f"{e['contentType'][:40]:<40} {e['url'][:160]}"
            + (f"  server={e['server']}" if e["server"] else "")
            + (f"  post={e['postData']!r}" if e["postData"] else "")
        )
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manual-timeout", type=int, default=300,
                        help="seconds to wait for manual CAPTCHA completion per listing")
    parser.add_argument("--no-next-page", action="store_true",
                        help="do not click the page-2 link to observe pagination")
    parser.add_argument("--channel", choices=["chrome", "msedge"], default=None,
                        help="use an installed Chrome/Edge instead of Playwright's bundled Chromium")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    DATA_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    started = datetime.now().isoformat(timespec="seconds")
    results: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, channel=args.channel)
        context = browser.new_context(locale="ru-RU", viewport={"width": 1400, "height": 900})
        page = context.new_page()
        net = NetworkRecorder(page)
        try:
            results.append(inspect_listing(
                page, net, "original", DEFAULT_BASE_URL,
                DATA_DIR / "sample_page.html", DATA_DIR / "sample_page.png",
                args.manual_timeout, click_next=not args.no_next_page,
            ))
            time.sleep(3)
            results.append(inspect_listing(
                page, net, "checked", CHECKED_URL,
                DATA_DIR / "sample_page_checked.html", DATA_DIR / "sample_page_checked.png",
                args.manual_timeout, click_next=not args.no_next_page,
            ))
            net.flush_bodies()
        finally:
            write_redacted(REPORT_PATH, build_report(results, net, started))
            details = {
                "results": results,
                "network": [{k: v for k, v in e.items() if k != "_body"} for e in net.entries],
            }
            write_redacted(DETAILS_PATH, fmt(details))
            context.close()
            browser.close()

    print(f"\nReport written to {REPORT_PATH}")
    print(f"Details written to {DETAILS_PATH}")
    outputs = [REPORT_PATH, DETAILS_PATH, DATA_DIR / "sample_page.html", DATA_DIR / "sample_page_checked.html"]
    findings = audit_outputs(outputs)
    if findings:
        print(f"\nCredential audit: {len(findings)} line(s) to review manually before sharing:")
        for line in findings[:25]:
            print(f"  {line}")
    else:
        print("\nCredential audit: no cookies, session tokens or auth headers found in outputs.")
    for r in results:
        print(f"  {r['label']}: accessible={r.get('accessible')} error={r.get('error')}")
    return 0 if results and all(r.get("accessible") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
