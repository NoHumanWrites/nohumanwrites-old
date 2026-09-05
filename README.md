# NoHumanWrites

Find the bytes a human typed inside machine-written work.

Every AI-text detector asks "did a machine write this?". In an agent-first workflow the machine writes by default, and the interesting spans are the ones a person inserted by hand without the tests, sources and logs the agent's work carries. NoHumanWrites attributes files line by line against the agent harness's own logs, falls back to git authorship, and keeps a statistical layer only as triage.

## Try it in ten seconds

```bash
git clone <this repo> && cd nohumanwrites
python3 nohumanwrites.py check ~/my-project        # how much of it carries machine provenance, and which lines don't
python3 nohumanwrites.py check ~/my-project --badge   # a README badge
python3 nohumanwrites.py setup                     # sign every future agent edit (Claude Code hook + dedicated key)
```

What you get back depends on the evidence available, and the tool tells you which it used:

| Evidence found | What the score means |
|---|---|
| A signed ledger (`.nhw/attest.jsonl`) in the repository | Exact and verifiable: every scored line either sits in a signed, still-matching machine hunk or it doesn't |
| Claude Code transcripts on this machine | Exact for writes the harness logged, but unsigned; covers only the log window |
| Neither | No score. Writing style is not evidence (paper §6), so the tool says "no provenance" and shows how to get some |

An unattested line means *typed by hand, or written through a channel with no hook*. The tool never claims a line is human. A third state, **unverifiable**, appears when a ledger exists but none of its records verify with *your* keys; no percentage is printed then, and `--badge` is refused. The trust root is always yours (`~/.nhw/allowed_signers` or `--signers FILE`); a repository's own keys are ignored unless you pass `--trust-repo-signers`, and the output says where trust came from. Read `SECURITY.md` before trusting a number: the key proves the channel, not the author.

## Not only code: books, lyrics, poems, sheet music

The same ledger scores any text in the unit a reader edits. Code by line. Books, articles and essays by **paragraph**, with a paragraph whose sentences mostly survive reported as *edited* rather than lost. Lyrics and poetry by **verse line and stanza** (detected from the shape of the text, or force it with `--profile verse`). Sheet music by **bar** (ABC notation) or **measure** (MusicXML). Exported `.docx`, `.epub` and `.odt` files are unpacked and their paragraphs matched against the ledger, so provenance survives export. Media files are checked for a C2PA Content Credentials manifest and otherwise reported as "no provenance".

Two ways text gets into the ledger. Anything Claude Code writes inside a git repository is signed by the hook. Anything you receive from an AI *elsewhere* (claude.ai, ChatGPT, a music model) you sign the moment you save it:

```bash
python3 nohumanwrites.py import chapter1.md --from claude.ai      # sign the chapter as received
python3 nohumanwrites.py import song.txt --from suno --clipboard  # save the clipboard into song.txt, then sign it
# ... write, rewrite, publish ...
python3 nohumanwrites.py check chapter1.md    # → 14 paragraphs: 11 attested, 2 edited, 1 unattested (paragraph 9)
```

### Published pages, LinkedIn, podcasts, streamed tracks

Point `check` at a URL and it prints a **provenance card**: what the platform *declares* (author, date, generator, any AI-disclosure wording, Content Credentials on the lead image or in the audio) in one column, and what is *verifiable against your ledger* in the other.

```bash
python3 nohumanwrites.py import linkedin-draft.md --from claude.ai      # sign the draft the AI gave you
# ... you rewrite paragraph 2, add a closing line, publish ...
python3 nohumanwrites.py check https://www.linkedin.com/posts/...  --repo ~/posts
#   4 paragraph(s): 2 attested (signed before publishing), 1 edited, 1 unattested
#   paragraph 2: edited · paragraph 4: unattested
```

LinkedIn shows a login wall to anonymous readers, so for LinkedIn save the post page from your browser and run `check saved-post.html --repo ~/posts`; Medium, Substack and ordinary blogs work from the URL. A podcast RSS feed gets a per-episode card (disclosure wording, C2PA in the first 2 MB of audio, transcript link) and, when a transcript exists, the transcript matched against the script you signed before recording. A Spotify track gets a card of declared credits versus the empty verifiable column that a stream necessarily has.

What this cannot do, on purpose: look at a poem nobody signed and tell you whether a person wrote it. Nothing can (paper §6). It tells you what changed after the machine's version was signed, which is the question a publisher, a co-writer or a rights holder can act on.

Keep the number in a pull request with a two-line GitHub Action:

```yaml
- run: python3 nohumanwrites/nohumanwrites.py check . --json > nhw.json
- run: python3 -c "import json;r=json.load(open('nhw.json'));print(f\"{100*(r['lines']-r['unattested'])/r['lines']:.0f}% attested\")"
```

## Layout

```
nohumanwrites.py     the public checker: check / setup, picks the best evidence available and says which
nhw/common.py       one normalisation + repo helper shared by hook, verifier and checker (byte-stable hashes)
SECURITY.md         the three sentences that govern every number this tool prints
tests/test_ledger.py   12 end-to-end checks: sign, verify, hand edits, partial-line edits, duplicates, tampering, forgery, malformed records
nhw/attest.py       layer 1: provenance from Claude Code transcripts (Write/Edit tool calls)
nhw/gitmode.py      layer 1b: git blame + Co-Authored-By trailers
nhw/stat.py         layer 3: inverted AI-tell scoring (sloptrim), labelled weak
nhw/hook.py         production layer 1: Claude Code PostToolUse hook → signed .nhw/attest.jsonl (ssh-ed25519)
nhw/verify.py       verifier: signature check + hunk match, per file or per git diff
cli.py              CLI tying the layers together
eval/separability.py   human-vs-machine ground-truth test on your own transcripts
eval/*.json         results from the run reported in the paper
paper/nohumanwrites.md  the paper
business.md         the business plan
video-outline.md    the launch video
SPEC.md             proposed signed-ledger format (.nhw/attest.jsonl)
```

## Run

```bash
python3 cli.py index            # build the attested-line index from ~/.claude/projects
python3 cli.py file <path>...   # attribute files; add --show to list unattested lines
python3 cli.py git <path>...    # git-based attribution
python3 cli.py stat <path>...   # statistical triage (weak)
python3 eval/separability.py    # reproduce the AUC / base-rate numbers on your data
```

Standard library only. Nothing leaves the machine.

## Results on the author's machine (2026-09-04)

- 2,716 logged agent writes over 5 weeks → 72,681 attested lines.
- Code written inside the harness: 96 % median line attestation.
- The PoC's own source, written by the same agent through a shell heredoc: 9 %. Unlogged channels look human. That is why the product is a signed ledger, not log forensics.
- Statistical layer: AUC 0.91 for machine-vs-human, but 60 % of machine passages carry no tell, so as a human detector its precision at 5 % prevalence is about 8 %.

## What this is not

No AI-text detector for grading people. No humaniser. No watermark removal. See paper §7.

Publisher: Plus de Fun Agency (a line of PLUS DE FUN Sàrl, Geneva). Licence: Apache-2.0 (proposed).

Project home: nohumanwrites.org (domain registered 2026-09-04; site to follow).
