#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Published pages and podcasts: a signed draft matched against what went live.

Run:  python3 tests/test_web.py     (exit 0 = pass)
"""
import io, json, os, shutil, subprocess, sys, tempfile, contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "nohumanwrites.py")
sys.path.insert(0, ROOT)

DRAFT = """Most teams now let an agent write the first version of everything. That is not the risk. The risk is the line a person adds at 23:40 that nobody reviews.

NoHumanWrites is a small tool that signs what the machine wrote at the moment it is written, so that later you can see exactly which paragraphs were changed by hand.

It never says a paragraph is human. It says a paragraph is unattested, and leaves the judgement to you.
"""


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    work = tempfile.mkdtemp(prefix="nhw-web-"); checks = 0
    try:
        home = os.path.join(work, "home"); repo = os.path.join(work, "posts")
        os.makedirs(os.path.join(home, ".nhw")); os.makedirs(repo); run(["git", "init", "-q", repo])
        key = os.path.join(home, ".nhw", "id_nhw")
        assert run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key]).returncode == 0
        pub = open(key + ".pub").read().split()[:2]
        open(os.path.join(home, ".nhw", "allowed_signers"), "w").write(f'nhw namespaces="nhw" {pub[0]} {pub[1]}\n')
        env = dict(os.environ, HOME=home, NHW_KEY=key)

        # 1. the LinkedIn draft, as received from the AI, is signed
        draft = os.path.join(repo, "linkedin-draft.md"); open(draft, "w").write(DRAFT)
        r = run([sys.executable, CLI, "import", draft, "--from", "claude.ai"], env=env)
        assert "1 signed record" in r.stdout, r.stdout + r.stderr; checks += 1

        # 2. the post goes live with one sentence of paragraph 1 changed and a new closing line; the page is saved from the browser
        live = DRAFT.replace("That is not the risk.", "That is not the danger, and never was.")
        paras = [p for p in live.split("\n\n") if p.strip()] + ["Try it, and tell me what your own ratio is."]
        page = ('<html><head><title>Why I stopped trusting my own edits | LinkedIn</title>'
                '<meta name="author" content="Arnaud Chretien"><meta property="article:published_time" content="2026-09-05">'
                '</head><body><nav>menu</nav><article>' + "".join(f"<p>{p.strip()}</p>" for p in paras) + '</article></body></html>')
        saved = os.path.join(work, "saved-post.html"); open(saved, "w").write(page)
        r = run([sys.executable, CLI, "check", saved, "--repo", repo], env=env)
        assert "provenance card for a published page" in r.stdout and "author        Arnaud Chretien" in r.stdout, r.stdout + r.stderr
        assert "4 paragraph(s): 2 attested (signed before publishing), 1 edited, 1 unattested" in r.stdout, r.stdout; checks += 1
        assert "paragraph 1: edited" in r.stdout and "paragraph 4: unattested" in r.stdout, r.stdout; checks += 1

        # 3. without a ledger the page gets "no provenance", never "human"
        r = run([sys.executable, CLI, "check", saved], env=dict(env, HOME=os.path.join(work, "nohome")), cwd=work)
        assert "no provenance" in r.stdout and "human" not in r.stdout.lower().replace("nohumanwrites", ""), r.stdout; checks += 1

        # 4. a podcast feed: declared fields, per-episode disclosure and transcript, script matched against the ledger
        import nohumanwrites as nhw
        script = os.path.join(repo, "episode-12-script.md"); open(script, "w").write(DRAFT)
        run([sys.executable, CLI, "import", script, "--from", "claude.ai"], env=env)
        tr_path = os.path.join(work, "ep12.txt"); open(tr_path, "w").write(DRAFT.replace("That is not the risk.", "That is not the risk, really."))
        feed = f"""<?xml version="1.0"?><rss xmlns:itunes="i" xmlns:podcast="p"><channel><title>Love Over Fear</title>
        <itunes:author>Arnaud</itunes:author><generator>Test</generator>
        <item><title>Ep 12 — Who wrote this?</title><enclosure url="file://{tr_path}" type="audio/mpeg"/>
        <podcast:transcript url="file://{tr_path}" type="text/plain"/><description>Synthetic voice for the intro.</description></item>
        <item><title>Ep 11</title></item></channel></rss>"""
        os.environ["HOME"] = home; os.environ["NHW_KEY"] = key
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            nhw.podcast_card(feed, "https://example.org/feed.xml", repo)
        out = buf.getvalue()
        assert "Love Over Fear" in out and "episodes      2 in feed" in out and "Synthetic voice" in out, out; checks += 1
        assert "vs ledger" in out and "2 attested / 1 edited / 0 unattested" in out, out; checks += 1
        assert "Ep 11" in out and "transcript   none published" in out, out; checks += 1

        print(f"ok: {checks} checks passed"); return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
