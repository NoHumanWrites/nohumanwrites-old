#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""nohumanwrites — check how much of what you use was written through an attested machine channel.

  python3 nohumanwrites.py check <path>...              score files or a directory
  python3 nohumanwrites.py check <path> --json          machine-readable
  python3 nohumanwrites.py check <path> --badge         one-line badge for a README (refused when there is no score)
  python3 nohumanwrites.py check <path> --transcripts   score against Claude Code logs even if a ledger exists
  python3 nohumanwrites.py check <path> --signers FILE  use this allowed-signers file
  python3 nohumanwrites.py check <path> --trust-repo-signers   accept the repository's own keys (prints a warning)
  python3 nohumanwrites.py check <path> --profile prose|verse|abc|musicxml|code   override the auto-detected unit
  python3 nohumanwrites.py check <web-url> [--repo DIR]  provenance card for a published page (LinkedIn, Medium, a blog):
                                                        declared author/date/disclosure, Content Credentials on the lead
                                                        image, and its paragraphs matched against your ledger
  python3 nohumanwrites.py check <podcast-rss-url> [--repo DIR]   provenance card for a podcast: per-episode disclosure,
                                                        C2PA in the audio, transcript matched against a signed script
  python3 nohumanwrites.py check <spotify-track-url>    provenance card for a streamed track (declared vs verifiable)
  python3 nohumanwrites.py check saved.html --repo DIR  same as the web card, for a page saved from the browser (LinkedIn)
  python3 nohumanwrites.py import <file> --from claude.ai     sign a text you received from an AI elsewhere, as received
  python3 nohumanwrites.py import <file> --from chatgpt --clipboard   same, saving the clipboard into <file> first
  python3 nohumanwrites.py setup                        create the signing key + install the Claude Code hook

Works beyond code.  Each file is scored in the unit a reader edits: code by line; books, articles and
essays by paragraph (a changed paragraph whose sentences mostly survive is reported as "edited"); lyrics
and poetry by verse line and stanza; sheet music by bar (ABC notation) or measure (MusicXML).  Exported
.docx / .epub / .odt are unpacked and their paragraphs matched against the ledger, so provenance survives
export.  Media files (.jpg .png .pdf .mp4 .mp3 …) are checked for a C2PA Content Credentials manifest and
otherwise reported as "no provenance"; the tool never guesses from style.

Evidence is used in this order, and the report says which one it found:
  1. a signed ledger in the repository (.nhw/attest.jsonl)   — exact, verifiable with YOUR keys
  2. Claude Code transcripts on this machine (~/.claude/projects) — exact for logged writes, unsigned, strong
     matches only (the line was written to that path)
  3. nothing                                                    — then the honest answer is "no provenance",
                                                                  never a guess from writing style.
Three states: attested / unattested / unverifiable.  "Unattested" means typed by hand OR written through a
channel with no hook; this tool never says "human".  Standard library + OpenSSH.  Nothing leaves the machine.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile

__version__ = "0.1.0-rc1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nhw import attest, verify  # noqa: E402
from nhw.common import MIN_LEN, norm_text, read_text, repo_root, profile_for, decode_text  # noqa: E402

SKIP_DIRS = {"node_modules", "__pycache__", "dist", "build", ".git", ".nhw"}
EXPORT_EXT = {".docx", ".epub", ".odt"}                       # zipped-XML documents: paragraphs are extracted
MEDIA_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".tif", ".tiff", ".pdf", ".mp4", ".mov", ".m4a",
             ".mp3", ".wav", ".flac", ".aiff", ".avi", ".mkv", ".mid", ".midi"}


def extract_document(path):
    """Paragraph text from .docx / .epub / .odt (zip of XML), or None."""
    import html, re, zipfile
    ext = os.path.splitext(path)[1].lower()
    try:
        z = zipfile.ZipFile(path)
    except Exception:
        return None
    paras = []
    def add_from_xml(xml, para_tag):
        for m in re.finditer(rf"<{para_tag}\b.*?</{para_tag}>", xml, re.S):
            t = re.sub(r"<[^>]+>", "", m.group(0))
            t = html.unescape(t).strip()
            if t:
                paras.append(t)
    try:
        if ext == ".docx":
            add_from_xml(z.read("word/document.xml").decode("utf-8", "replace"), "w:p")
        elif ext == ".odt":
            add_from_xml(z.read("content.xml").decode("utf-8", "replace"), "text:p")
        elif ext == ".epub":
            for n in sorted(z.namelist()):
                if n.lower().endswith((".xhtml", ".html", ".htm")):
                    xml = z.read(n).decode("utf-8", "replace")
                    for tag in ("h1", "h2", "h3", "p"):
                        add_from_xml(xml, tag)
    except KeyError:
        return None
    return "\n\n".join(paras)


def content_credentials(path):
    """Does a media file carry a C2PA Content Credentials manifest?  Presence only; verify with c2patool."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8_000_000)
    except OSError:
        return None
    return any(m in head for m in (b"c2pa", b"contentauth", b"jumb\x00\x00\x00", b"C2PA"))


def list_files(paths):
    """→ (text files, exported documents, media files)."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
                for f in files:
                    fp = os.path.join(root, f)
                    if not f.startswith(".") and os.path.isfile(fp) and os.path.getsize(fp) < 50_000_000:
                        out.append(fp)
        elif os.path.isfile(p):
            out.append(p)
    text, docs, media = [], [], []
    for f in out:
        ext = os.path.splitext(f)[1].lower()
        if ext in EXPORT_EXT:
            docs.append(f)
        elif ext in MEDIA_EXT:
            media.append(f)
        elif os.path.getsize(f) < 2_000_000 and read_text(f) is not None:
            text.append(f)
    return text, docs, media


def considered(body):
    lines = norm_text(body).split("\n")
    return [i for i in range(1, len(lines) + 1) if len(lines[i - 1].strip()) >= MIN_LEN]


def check_ledger(repo, files, docs=(), explicit=None, trust_repo=False, force_profile=None):
    records, malformed = verify.load_ledger(repo)
    if not records and not malformed:
        return None
    signers, src = verify.allowed_signers(repo, explicit, trust_repo)
    fps = verify.fingerprints(signers) if signers else {}
    tot = un = 0; per = []; verified_total = unverified_total = 0; unverifiable_files = 0
    for f in files:
        rel = os.path.relpath(os.path.realpath(f), repo)
        body = read_text(f) or ""
        profile = force_profile or profile_for(f, body)
        if profile == "code":
            covered, verified, stale, unverified = verify.attested_lines(repo, rel, records, signers, fps)
            verified_total += verified; unverified_total += unverified
            cons = considered(body)
            if not cons:
                continue
            if verified == 0 and unverified > 0:
                unverifiable_files += 1; per.append({"file": rel, "profile": profile, "units": len(cons), "state": "unverifiable"}); continue
            u = [i for i in cons if i not in covered]
            tot += len(cons); un += len(u)
            per.append({"file": rel, "profile": profile, "unit": "line", "units": len(cons), "unattested": u, "state": "scored"})
        else:
            labels, status, verified, unverified = verify.unit_coverage(body, profile, records, signers, fps, rel)
            verified_total += verified; unverified_total += unverified
            if not labels:
                continue
            if verified == 0 and unverified > 0:
                unverifiable_files += 1; per.append({"file": rel, "profile": profile, "units": len(labels), "state": "unverifiable"}); continue
            u = [labels[i] for i, s in enumerate(status) if s == "unattested"]
            e = [labels[i] for i, s in enumerate(status) if s == "edited"]
            tot += len(labels); un += len(u)
            per.append({"file": rel, "profile": profile, "unit": labels[0].split()[0] if labels else "unit",
                        "units": len(labels), "unattested": u, "edited": e, "state": "scored"})
    for d in docs:                                   # exported documents: paragraphs matched anywhere in the ledger
        text = extract_document(d)
        if not text:
            per.append({"file": d, "profile": "document", "state": "unreadable"}); continue
        labels, status, verified, unverified = verify.unit_coverage(text, "prose", records, signers, fps, None)
        if not labels:
            continue
        if verified == 0 and unverified > 0:
            unverifiable_files += 1; per.append({"file": d, "profile": "document", "units": len(labels), "state": "unverifiable"}); continue
        u = [labels[i] for i, s in enumerate(status) if s == "unattested"]
        e = [labels[i] for i, s in enumerate(status) if s == "edited"]
        tot += len(labels); un += len(u)
        per.append({"file": d, "profile": "document (exported; matched against the whole ledger)", "unit": "paragraph",
                    "units": len(labels), "unattested": u, "edited": e, "state": "scored"})
    state = "scored" if tot else ("unverifiable" if unverifiable_files else "no-evidence")
    return {"state": state, "evidence": "signed ledger (.nhw/attest.jsonl)", "trust_root": src, "keys": len(fps),
            "records_valid": len(records), "records_malformed": malformed,
            "records_verified": verified_total, "records_unverified": unverified_total,
            "files_unverifiable": unverifiable_files, "lines": tot, "unattested": un, "files": per}


def media_report(media):
    out = []
    for m in media:
        cc = content_credentials(m)
        out.append({"file": m, "content_credentials": cc})
    return out


def check_transcripts(files):
    if not os.path.isdir(attest.LOGDIR):
        return None
    pf, stats = attest.load_index()
    if not stats.get("records"):
        return None
    tot = un = 0; per = []
    for f in files:
        r = attest.attribute(f, pf, MIN_LEN)
        if "error" in r or not r["considered"]:
            continue
        # strong matches only: the agent wrote that line to THAT path; "weak" (same line elsewhere) is not evidence
        strong_un = r["considered"] - r["strong"]
        tot += r["considered"]; un += strong_un
        per.append({"file": f, "lines": r["considered"], "unattested_count": strong_un, "unattested": [i for i, _ in r["unattested"]]})
    return {"state": "scored" if tot else "no-evidence",
            "evidence": f"Claude Code transcripts on this machine ({stats['records']} logged writes; unsigned; strong matches only)",
            "trust_root": "none (unsigned logs)", "lines": tot, "unattested": un, "files": per}


def render(res, badge=False, as_json=False):
    if as_json:
        print(json.dumps(res, indent=1)); return 0
    if res is None or res["state"] == "no-evidence":
        print("NoHumanWrites: no provenance available here.")
        print("  No signed ledger for these files and no agent transcripts on this machine.")
        print("  Writing style is not evidence (paper §6), so no score is given.")
        print("  To get one: python3 nohumanwrites.py setup   (signs every future agent edit)")
        return 3
    if res["state"] == "unverifiable":
        print(f"NoHumanWrites: UNVERIFIABLE — ledger present ({res['records_valid']} record(s)) but none verify with your keys "
              f"(trust root: {res['trust_root']}). No score. Use --signers FILE, or --trust-repo-signers if you accept the repo's own keys.")
        return 4
    pct = 100 * (res["lines"] - res["unattested"]) / res["lines"]
    if badge:
        colour = "2ea44f" if pct >= 90 else "e0b23a" if pct >= 50 else "d23a2e"
        print(f"![NoHumanWrites](https://img.shields.io/badge/NoHumanWrites-{pct:.0f}%25_attested-{colour})"); return 0
    print(f"NoHumanWrites: {pct:.0f}% attested — {res['lines'] - res['unattested']} of {res['lines']} units")
    print(f"  evidence: {res['evidence']}; trust root: {res['trust_root']}")
    if res.get("records_malformed"):
        print(f"  {res['records_malformed']} malformed ledger record(s) ignored")
    if res.get("files_unverifiable"):
        print(f"  {res['files_unverifiable']} file(s) unverifiable (records present, none verify with your keys) — not scored")
    scored = [f for f in res["files"] if f.get("state", "scored") == "scored" and f.get("units", f.get("lines", 0))]
    def n_un(f): return len(f.get("unattested", [])) or f.get("unattested_count", 0)
    worst = sorted(scored, key=lambda f: -n_un(f) / f.get("units", f.get("lines", 1)))[:8]
    for f in worst:
        u = f.get("unattested", []); e = f.get("edited", [])
        unit = f.get("unit", "line")
        if n_un(f) or e:
            where = ", ".join(str(x) for x in u[:8]) + ("…" if len(u) > 8 else "")
            extra = f"; {len(e)} edited {unit}(s): {', '.join(str(x) for x in e[:4])}{'…' if len(e) > 4 else ''}" if e else ""
            print(f"  {f['file']} [{f.get('profile', 'code')}]: {n_un(f)} unattested {unit}(s)" + (f" → {where}" if where else "") + extra)
    if res["unattested"] == 0:
        print("  every considered unit carries machine provenance.")
    else:
        print("  unattested = typed by hand, or written through a channel with no hook; edited = a signed paragraph someone changed part of. Review those first.")
    for m in res.get("media", []):
        cc = m["content_credentials"]
        print(f"  {m['file']}: " + ("carries a C2PA Content Credentials manifest (provenance may be verifiable with c2patool)" if cc
                                    else "no Content Credentials manifest found — no provenance" if cc is False else "unreadable"))
    return 0


def stream_card(url):
    """A provenance card for a streamed track (Spotify link): what is DECLARED by the platforms
    versus what is VERIFIABLE.  A stream carries no file we can hash, no C2PA manifest and no
    watermark key, so the verifiable column is empty by construction; the card says so instead of guessing."""
    import re, urllib.parse, urllib.request
    def get(u, headers=None):
        req = urllib.request.Request(u, headers={"User-Agent": "NoHumanWrites/0.1", **(headers or {})})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    m = re.search(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]+)", url)
    if not m:
        print("only open.spotify.com/track/... links are supported for now"); return 2
    tid = m.group(1); declared = {}
    try:
        emb = get(f"https://open.spotify.com/embed/track/{tid}", {"User-Agent": "Mozilla/5.0"})
        j = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', emb, re.S)
        ent = json.loads(j.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
        declared["title"] = ent.get("name"); declared["artists"] = [a["name"] for a in ent.get("artists", [])]
        declared["duration"] = f"{ent.get('duration', 0) // 60000}:{(ent.get('duration', 0) // 1000) % 60:02d}"
    except Exception as e:
        print(f"could not read the Spotify page: {e}"); return 1
    # cross-platform declared metadata (Deezer public API): label, ISRC, contributors, catalogue size
    try:
        q = urllib.parse.quote(f'artist:"{declared["artists"][0]}" track:"{declared["title"]}"')
        d = json.loads(get(f"https://api.deezer.com/search/track?q={q}")).get("data", [])
        if d:
            t = json.loads(get(f"https://api.deezer.com/track/{d[0]['id']}"))
            a = json.loads(get(f"https://api.deezer.com/album/{t['album']['id']}"))
            declared["isrc"] = t.get("isrc"); declared["release_date"] = t.get("release_date") or a.get("release_date")
            declared["label"] = a.get("label"); declared["contributors"] = [(c.get("name"), c.get("role")) for c in t.get("contributors", [])]
            art = json.loads(get(f"https://api.deezer.com/artist/{t['artist']['id']}"))
            albs = json.loads(get(f"https://api.deezer.com/artist/{t['artist']['id']}/albums?limit=100")).get("data", [])
            dates = sorted(x["release_date"] for x in albs if x.get("release_date"))
            declared["catalogue"] = f"{art.get('nb_album')} releases on Deezer" + (f", {dates[0]} → {dates[-1]}" if dates else "") + f"; {art.get('nb_fan')} fans"
    except Exception:
        pass
    print(f"NoHumanWrites — provenance card for a streamed track")
    print(f"  {declared.get('title')} — {', '.join(declared.get('artists', []))}  ({declared.get('duration')})")
    print("  DECLARED by the platforms (claims, not provenance):")
    for k in ("label", "release_date", "isrc", "contributors", "catalogue"):
        if declared.get(k) is not None:
            print(f"    {k:13s} {declared[k]}")
    if not any(declared.get(k) for k in ("contributors",)) or all(r in (None, "Main") for _, r in declared.get("contributors", [])):
        print("    credits       no songwriter / composer / producer credit published on the platforms queried")
    print("  VERIFIABLE provenance: none available.")
    print("    no signed ledger (the writer's workflow was not instrumented), no C2PA manifest (a stream is not a file),")
    print("    no watermark verdict (audio-model watermark keys are not public).")
    print("  Verdict: no provenance. The declared credits are the artist's or label's statement; nothing here confirms or")
    print("  denies a human or a machine wrote the song, and NoHumanWrites will not guess from the sound.")
    return 3


def _fetch(url, limit=8_000_000, headers=None):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; NoHumanWrites/0.1)", **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read(limit), r.headers.get("Content-Type", ""), r.geturl()


def html_to_parts(page):
    """→ (meta dict, [paragraph texts]) from an HTML page: title, author/date/generator metas, JSON-LD author,
    og:image, AI-disclosure hints, and the paragraphs of the main article (or body)."""
    import html as h, re
    meta = {}
    def m(pattern):
        x = re.search(pattern, page, re.I | re.S); return h.unescape(x.group(1)).strip() if x else None
    meta["title"] = m(r"<title[^>]*>(.*?)</title>") or m(r'property="og:title" content="([^"]*)"')
    meta["author"] = m(r'name="author" content="([^"]*)"') or m(r'property="article:author" content="([^"]*)"')
    meta["published"] = m(r'property="article:published_time" content="([^"]*)"') or m(r'name="date" content="([^"]*)"')
    meta["generator"] = m(r'name="generator" content="([^"]*)"')
    meta["og_image"] = m(r'property="og:image" content="([^"]*)"')
    for ld in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.I | re.S):
        try:
            d = json.loads(ld); d = d if isinstance(d, dict) else (d[0] if d else {})
            a = d.get("author") or d.get("creator")
            if a and not meta.get("author"):
                meta["author"] = ", ".join(x.get("name", "") for x in (a if isinstance(a, list) else [a]) if isinstance(x, dict)) or str(a)
            if d.get("datePublished") and not meta.get("published"):
                meta["published"] = d["datePublished"]
            for k in ("creditText", "creativeWorkStatus", "isBasedOn"):
                if d.get(k): meta[k] = str(d[k])[:120]
        except Exception:
            pass
    disc = re.findall(r"(?i)(AI[- ]generated|generated (?:with|by) (?:AI|an AI|artificial intelligence)|written with (?:the help of )?AI|content credentials|trainedAlgorithmicMedia|compositeWithTrainedAlgorithmicMedia)", page)
    meta["ai_disclosure_hints"] = sorted(set(x if isinstance(x, str) else x[0] for x in disc))[:5]
    body = re.search(r"<article\b.*?</article>", page, re.I | re.S)
    body = body.group(0) if body else page
    body = re.sub(r"<(script|style|nav|footer|header|aside)\b.*?</\1>", " ", body, flags=re.I | re.S)
    paras = []
    for p in re.findall(r"<(?:p|h1|h2|h3|li|blockquote)\b[^>]*>(.*?)</(?:p|h1|h2|h3|li|blockquote)>", body, re.I | re.S):
        t = h.unescape(re.sub(r"<[^>]+>", " ", p)); t = " ".join(t.split())
        if len(t) >= 25:
            paras.append(t)
    if len(paras) < 2:                                     # fallback: text blocks split on double breaks
        t = h.unescape(re.sub(r"<[^>]+>", "\n", body))
        paras = [" ".join(x.split()) for x in re.split(r"\n\s*\n", t) if len(" ".join(x.split())) >= 40]
    return meta, paras


def web_card(url, repo=None, explicit=None, trust_repo=False):
    """Provenance card for a published web page (LinkedIn/Medium/Substack/blog): declared metadata, disclosure
    hints, Content Credentials on the lead image, and — the part that means something — its paragraphs matched
    against your ledger, so a post you signed before publishing shows what changed."""
    try:
        raw, ctype, final = _fetch(url)
    except Exception as e:
        print(f"could not fetch the page: {e}"); return 1
    page = raw.decode("utf-8", "replace")
    if "linkedin.com" in final and ("authwall" in final or "Sign In" in page[:20000] or "login" in final):
        print("LinkedIn shows a login wall to anonymous readers. Save the post page from your browser (File → Save) and run:")
        print("  python3 nohumanwrites.py check saved-post.html --repo <folder with your .nhw ledger>"); return 2
    if "xml" in ctype or page.lstrip().startswith("<?xml") or "<rss" in page[:2000] or "<feed" in page[:2000]:
        return podcast_card(page, final, repo, explicit, trust_repo)
    meta, paras = html_to_parts(page)
    return _web_report(final, meta, paras, repo, explicit, trust_repo)


def _web_report(final, meta, paras, repo, explicit, trust_repo):
    print("NoHumanWrites — provenance card for a published page")
    print(f"  {meta.get('title') or final}")
    print("  DECLARED (claims, not provenance):")
    for k in ("author", "published", "generator", "creditText", "creativeWorkStatus"):
        if meta.get(k): print(f"    {k:13s} {meta[k]}")
    print(f"    disclosure    " + (", ".join(meta["ai_disclosure_hints"]) if meta.get("ai_disclosure_hints") else "no AI-disclosure wording or credential tags found on the page"))
    if meta.get("og_image"):
        try:
            img, _, _ = _fetch(meta["og_image"])
            cc = any(x in img for x in (b"c2pa", b"contentauth", b"jumb\x00\x00\x00", b"C2PA", b"trainedAlgorithmicMedia"))
            print(f"    lead image    " + ("carries a C2PA / IPTC provenance marker (verify with c2patool)" if cc else "no Content Credentials"))
        except Exception:
            pass
    print("  VERIFIABLE against your ledger:")
    records, malformed = verify.load_ledger(repo) if repo else ([], 0)
    if not paras:
        print("    no readable paragraphs on the page"); return 3
    if not records:
        print(f"    {len(paras)} paragraph(s) on the page, no ledger to compare with (pass --repo <folder with .nhw/>) → no provenance"); return 3
    signers, src = verify.allowed_signers(repo, explicit, trust_repo)
    fps = verify.fingerprints(signers) if signers else {}
    labels, status, ver, unver = verify.unit_coverage("\n\n".join(paras), "prose", records, signers, fps, None)
    if ver == 0 and unver > 0:
        print("    ledger records exist but none verify with your keys → unverifiable"); return 4
    att = status.count("attested"); ed = status.count("edited"); un = status.count("unattested")
    print(f"    {len(labels)} paragraph(s): {att} attested (signed before publishing), {ed} edited, {un} unattested")
    for l, s in zip(labels, status):
        if s != "attested": print(f"      {l}: {s}")
    print("  Verdict: " + ("every paragraph carries provenance from your signed draft." if un == 0 and ed == 0 else
                          "the unattested / edited paragraphs were changed or added after the signed draft; nothing here says by whom."))
    return 0


def podcast_card(xml, final, repo=None, explicit=None, trust_repo=False):
    """Provenance card for a podcast feed: declared metadata, per-episode transcript links, C2PA presence in the
    audio, and transcripts matched against your ledger (a script signed before recording)."""
    import html as h, re
    def m(pat, s=xml):
        x = re.search(pat, s, re.I | re.S); return h.unescape(x.group(1)).strip() if x else None
    print("NoHumanWrites — provenance card for a podcast feed")
    print(f"  {m(r'<channel>.*?<title>(.*?)</title>') or m(r'<feed.*?<title[^>]*>(.*?)</title>') or final}")
    print("  DECLARED (claims, not provenance):")
    for k, pat in (("author", r"<itunes:author>(.*?)</itunes:author>"), ("owner", r"<itunes:email>(.*?)</itunes:email>"),
                   ("generator", r"<generator>(.*?)</generator>"), ("guid/host", r"<podcast:person[^>]*>(.*?)</podcast:person>")):
        v = m(pat)
        if v: print(f"    {k:13s} {v[:100]}")
    items = re.findall(r"<item>(.*?)</item>", xml, re.I | re.S)[:3]
    print(f"    episodes      {len(re.findall(r'<item>', xml, re.I))} in feed; checking the latest {len(items)}")
    records, malformed = verify.load_ledger(repo) if repo else ([], 0)
    signers, src = verify.allowed_signers(repo, explicit, trust_repo) if repo else (None, "none")
    fps = verify.fingerprints(signers) if signers else {}
    for it in items:
        title = m(r"<title>(.*?)</title>", it) or "(untitled)"
        enc = m(r'<enclosure[^>]*url="([^"]+)"', it)
        tr = re.findall(r'<podcast:transcript[^>]*url="([^"]+)"', it, re.I)
        ai = re.findall(r"(?i)(AI[- ]generated|generated (?:with|by) AI|synthetic voice|text-to-speech|<podcast:txt[^>]*>)", it)
        print(f"  — {title[:70]}")
        if enc:
            try:
                head, _, _ = _fetch(enc, limit=2_000_000, headers={"Range": "bytes=0-1999999"})
                cc = any(x in head for x in (b"c2pa", b"contentauth", b"jumb\x00\x00\x00", b"C2PA"))
                print("      audio        " + ("carries a C2PA marker in its first 2 MB (verify with c2patool)" if cc else "no Content Credentials in the first 2 MB"))
            except Exception:
                print("      audio        enclosure not fetchable")
        print("      disclosure   " + (", ".join(sorted(set(a if isinstance(a, str) else a[0] for a in ai)))[:120] if ai else "none in the episode metadata"))
        if tr:
            print(f"      transcript   {tr[0][:90]}")
            if records:
                try:
                    t, _, _ = _fetch(tr[0]); text = t.decode("utf-8", "replace")
                    text = re.sub(r"<[^>]+>", " ", text) if "<" in text[:200] else text
                    text = re.sub(r"^\d+\s*$|^\d\d:\d\d:\d\d[^\n]*$", "", text, flags=re.M)
                    labels, status, ver, unver = verify.unit_coverage(text, "prose", records, signers, fps, None)
                    if labels:
                        print(f"      vs ledger    {status.count('attested')} attested / {status.count('edited')} edited / {status.count('unattested')} unattested paragraph(s) "
                              f"(transcripts are noisy: only exact sentence matches count)")
                except Exception:
                    print("      vs ledger    transcript not fetchable")
        else:
            print("      transcript   none published — nothing to match against a signed script")
    print("  VERIFIABLE: only what the rows above say. A voice cannot be attested by this tool; a script can, if it was signed before recording.")
    return 0


def _opt(args, name):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def cmd_check(args):
    flags = {a for a in args if a.startswith("--")}
    explicit = _opt(args, "--signers"); profile = _opt(args, "--profile"); repo_opt = _opt(args, "--repo")
    skip = {"--signers", "--profile", "--repo"}
    paths = [a for i, a in enumerate(args) if not a.startswith("--") and not (i > 0 and args[i - 1] in skip)] or ["."]
    ledger_repo = repo_root(repo_opt) if repo_opt else None
    if len(paths) == 1 and paths[0].startswith(("http://", "https://")):
        if "open.spotify.com/" in paths[0]:
            return stream_card(paths[0])
        return web_card(paths[0], ledger_repo or repo_root(os.getcwd()), explicit, "--trust-repo-signers" in flags)
    files, docs, media = list_files(paths)
    if not files and not docs and not media:
        print("nothing to check (no readable files)"); return 2
    # a saved web page (.html/.htm) is read like a published page: paragraphs against the ledger
    if len(files) == 1 and files[0].lower().endswith((".html", ".htm")) and (ledger_repo or not repo_root(files[0])):
        meta, paras = html_to_parts(read_text(files[0]) or "")
        return _web_report(files[0], meta, paras, ledger_repo or repo_root(os.getcwd()), explicit, "--trust-repo-signers" in flags)
    repo = ledger_repo or repo_root(paths[0])
    res = None if "--transcripts" in flags else (check_ledger(repo, files, docs, explicit, "--trust-repo-signers" in flags, profile) if repo else None)
    if res is None:
        res = check_transcripts(files)
    if media:
        if res is None or res.get("state") == "no-evidence":
            res = {"state": "media-only", "files": [], "lines": 0, "unattested": 0}
        res["media"] = media_report(media)
        if res["state"] == "media-only":
            print("NoHumanWrites: no text to score; media files checked for Content Credentials only.")
            for m in res["media"]:
                cc = m["content_credentials"]
                print(f"  {m['file']}: " + ("carries a C2PA Content Credentials manifest (verify with c2patool)" if cc else "no Content Credentials manifest found — no provenance"))
            return 0
    return render(res, badge="--badge" in flags, as_json="--json" in flags)


def cmd_import(args):
    """Sign the units of a file a person saved from an AI elsewhere (claude.ai, ChatGPT, a music model),
    at the moment they receive it.  Later human edits then show up as unattested or edited."""
    from nhw import hook
    label = _opt(args, "--from") or "external-ai"
    paths = [a for i, a in enumerate(args) if not a.startswith("--") and not (i > 0 and args[i - 1] == "--from")]
    if "--clipboard" in args:
        if not paths:
            print("--clipboard needs a target path to save the clipboard into"); return 2
        clip = subprocess.run(["pbpaste"] if sys.platform == "darwin" else ["xclip", "-o", "-selection", "clipboard"],
                              capture_output=True, text=True)
        if clip.returncode or not clip.stdout.strip():
            print("clipboard is empty or unreadable"); return 2
        with open(paths[0], "w", encoding="utf-8") as f:
            f.write(clip.stdout)
    if not paths:
        print("usage: nohumanwrites.py import <file> [--from claude.ai|chatgpt|suno|...] [--clipboard]"); return 2
    if not os.path.exists(hook.KEY):
        print("no signing key yet — run: python3 nohumanwrites.py setup"); return 1
    total = 0
    for p in paths:
        text = read_text(p)
        if text is None:
            print(f"{p}: not UTF-8 text, skipped"); continue
        if not repo_root(p):
            print(f"{p}: not inside a git repository (the ledger lives in the repo); run `git init` there first"); continue
        n = hook.record_file(p, text, {"kind": "pasted-ai", "harness": label, "session": None})
        prof = profile_for(p, text)
        print(f"{p}: {n} signed record(s) as received from {label} [{prof} profile]")
        total += n
    return 0 if total else 1


def cmd_setup():
    r = subprocess.run(["bash", os.path.join(HERE, "setup-key.sh")])
    if r.returncode:
        return r.returncode
    settings = os.path.expanduser("~/.claude/settings.json")
    if os.path.islink(settings):
        print(f"{settings} is a symlink; add the hook by hand (see nhw/hook.py docstring)"); return 1
    cmd = f"python3 \"{os.path.join(HERE, 'nhw', 'hook.py')}\""
    try:
        s = json.load(open(settings, encoding="utf-8")) if os.path.exists(settings) else {}
    except json.JSONDecodeError:
        print(f"{settings} is not valid JSON; add the hook by hand (see nhw/hook.py docstring)"); return 1
    pt = s.setdefault("hooks", {}).setdefault("PostToolUse", [])
    if any("nhw/hook.py" in h.get("command", "") for m in pt for h in m.get("hooks", [])):
        print("hook already installed"); return 0
    pt.append({"matcher": "Write|Edit|MultiEdit", "hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
    if os.path.exists(settings):
        with open(settings + ".bak", "w", encoding="utf-8") as b, open(settings, encoding="utf-8") as o:
            b.write(o.read())
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(settings) or ".", prefix=".settings.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    os.replace(tmp, settings)
    print(f"hook installed in {settings} (backup: {settings}.bak)")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    if argv[0] in ("-V", "--version"):
        print(__version__); return 0
    if argv[0] == "check":
        return cmd_check(argv[1:])
    if argv[0] == "import":
        return cmd_import(argv[1:])
    if argv[0] == "setup":
        return cmd_setup()
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
