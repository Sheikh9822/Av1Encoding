"""
resolve_filename.py
Given a URL as argv[1], print the best human-readable filename.
Priority: filename= query param → Content-Disposition header → URL path segment.
"""
import sys
import re
import urllib.parse
import urllib.request

url = sys.argv[1]

# 1. Query param: ?filename= or ?file=
qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
fn = (qs.get("filename") or qs.get("file") or [None])[0]
if fn:
    print(urllib.parse.unquote(fn))
    sys.exit()

# 2. Content-Disposition header (CDN signed URLs like tb-cdn, bunny, etc.)
try:
    req = urllib.request.Request(
        url, method="HEAD",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        cd = r.headers.get("Content-Disposition", "")
    # Handles:  filename="Foo.mkv"  and  filename*=UTF-8''Foo.mkv
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";\r\n]+)", cd, re.IGNORECASE)
    if m:
        print(urllib.parse.unquote(m.group(1).strip(' "')))
        sys.exit()
except Exception:
    pass

# 3. URL path segment fallback
print(urllib.parse.unquote(urllib.parse.urlparse(url).path.split("/")[-1]))
