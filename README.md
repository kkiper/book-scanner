# magscan

magscan reads the front cover of each magazine in your collection and works out the magazine name, issue, cover date, publisher and cover stories. It then:

- **renames the file** to a template you choose, e.g. `National Geographic - 2024-07 - Issue 245.pdf`
- **writes a Booklore sidecar** (`<file>.metadata.json` + `<file>.cover.jpg`) with title, series, issue number, publish date, description, categories, etc.
- **embeds the title, author and description in the PDF** so Booklore picks them up on its first scan

Nothing is changed until you approve it. You review what was read in a small web UI, fix anything that's wrong, then apply. Every rename can be undone.

It handles PDF (including scans with no text layer), CBZ and EPUB.

## How it reads the cover

The first page is rendered to an image and sent to a vision model together with any text layer from the first few pages, the current file name and any metadata already embedded in the file. You pick the model:

| Provider | Accuracy | Cost | Notes |
|---|---|---|---|
| **Claude** (`claude-opus-5-5`, default) | best | roughly 3-4 cents per issue | needs an API key from [console.anthropic.com](https://console.anthropic.com) |
| Claude (`claude-haiku-5-5`) | good | about a tenth of a cent per issue | set `CLAUDE_MODEL=claude-haiku-5-5`; good for big back catalogues |
| **Ollama** (local, e.g. `qwen2.5vl:7b`) | ok on clean covers | free | runs on your server; needs a GPU to be reasonably fast |

The costs are rough estimates for one cover image plus page text. To get a feel for accuracy before you commit, try a few dozen issues first.

## Install on Unraid

1. Copy `unraid/magscan.xml` to `/boot/config/plugins/dockerMan/templates-user/` on your Unraid flash drive (or paste its URL into *Docker → Add Container → Template repositories*).
2. *Docker → Add Container*, pick **magscan** from the template list.
3. Set:
   - **Magazine library**: the same folder your Booklore library points to, e.g. `/mnt/user/media/magazines`
   - **Anthropic API key**, or set **Provider** to `ollama` and fill in **Ollama URL**
4. Apply, then click the container icon → **WebUI** (port 8484).

The image is built by GitHub Actions and published to `ghcr.io/kkiper/book-scanner:latest`. If the repository is private, either make the package public under *GitHub → Packages → book-scanner → Package settings*, or log in on Unraid once with `docker login ghcr.io`.

The container runs as `nobody:users` (99:100), like other Unraid apps, so the files it renames keep normal Unraid permissions.

### Docker Compose / anything else

```bash
docker compose up -d   # edit the volume paths in docker-compose.yml first
```

## Using it

1. **Scan library for files**: finds PDFs/CBZs/EPUBs that don't have a sidecar yet.
2. **Read covers of all new files**: runs in the background; the page refreshes itself.
3. Look through the **review** tab. Each row shows the current name, the proposed name and the confidence. Click an issue to see the cover next to what was read, and correct any field. The preview updates when you save.
4. **Apply** a selection, or *Apply all in review*.

Things that went wrong (unreadable PDF, API error) land in the **error** tab with the reason. You can re-read them after fixing the cause.

Once you trust the results, you can turn on **Apply automatically when the model is confident** and **Auto-scan every N minutes** in Settings. New magazines dropped into the folder will then be renamed and tagged on their own. Low-confidence ones still wait for review.

### Command line

```bash
docker exec -it magscan python -m magscan preview "/library/some file.pdf"   # dry run, changes nothing
docker exec -it magscan python -m magscan run            # scan + read covers, leave for review
docker exec -it magscan python -m magscan run --apply    # ...and apply everything
```

## Templates

Set the templates in **Settings**. A live preview shows the result as you type.

| Field | Example |
|---|---|
| `{magazine}` | National Geographic |
| `{issue}` / `{volume}` | 245 / 12 |
| `{year}` `{month}` `{day}` | 2024 7 1 |
| `{month_name}` / `{month_abbr}` | July / Jul |
| `{season}` | Summer |
| `{date}` | 2024-07 (as precise as the cover is) |
| `{date_text}` | July/August 2024 (as printed) |
| `{publisher}` | National Geographic Partners |
| `{headline}` | The main cover story |
| `{cover_lines}` | Other cover stories, joined with `; ` |
| `{topic}` | Science |
| `{language}` | en |
| `{original}` | the file's original name, without extension |

- Format numbers Python-style: `{month:02}` → `07`, `{issue:>03}` → `045`.
- Wrap optional parts in `[ ]`. They disappear when a field inside is empty, so `[ - Issue {issue}]` adds nothing for magazines without issue numbers. Use `[[` / `]]` for literal brackets.
- A `/` in the **file name** template creates folders under the library (or `OUTPUT_DIR`): `{magazine}/{year}/{magazine} - {year}-{month:02}`. Without a `/`, files are renamed in their current folder.
- Characters that aren't allowed in file names (`: ? * " < > | /`) are removed from values automatically.
- If two issues end up with the same name, the second gets ` (2)` appended.

Some examples:

```
{magazine} - {year}-{month:02}[ - Issue {issue}]          National Geographic - 2024-07 - Issue 245.pdf
{magazine}/{magazine} {year}-{month:02}                    National Geographic/National Geographic 2024-07.pdf
{magazine} #{issue} ({month_abbr} {year})                  National Geographic #245 (Jul 2024).pdf
```

## What gets written for Booklore

For `National Geographic - 2024-07 - Issue 245.pdf`:

```json
// National Geographic - 2024-07 - Issue 245.metadata.json
{
  "version": "1.0",
  "generatedAt": "2026-10-11T00:00:00Z",
  "generatedBy": "magscan",
  "metadata": {
    "title": "National Geographic - July 2024 (Issue 245)",
    "authors": ["National Geographic Partners"],
    "publisher": "National Geographic Partners",
    "publishedDate": "2024-07-01",
    "description": "The Secret Lives of Octopuses\n\nIn this issue: Saving the Amazon; Inside Petra",
    "language": "en",
    "pageCount": 148,
    "categories": ["Magazine", "Science"],
    "series": { "name": "National Geographic", "number": 245 }
  },
  "cover": { "source": "external", "path": "National Geographic - 2024-07 - Issue 245.cover.jpg" }
}
```

This matches the sidecar format Booklore (v2+) reads and writes itself. The magazine becomes the **series** and the issue number becomes the **series number**, so Booklore groups the issues of each magazine together and orders them.

To load the sidecars into Booklore, use **Settings → Library Metadata Settings → Import All** for the library, or **Metadata Center → Sidecar → Import** for a single book. Embedded PDF metadata is read by Booklore automatically when it first scans a file.

**Tip:** it works best to run magscan on files *before* Booklore imports them. You can point it at a staging folder and set `OUTPUT_DIR` to your Booklore library. If Booklore has already imported the files, it will see the new names on its next library scan.

## Settings reference (environment variables)

All of these are just starting values. The templates and toggles can be changed in the web UI, which saves them to `/config/settings.json`.

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | | Claude API key |
| `PROVIDER` | `anthropic` | `anthropic` or `ollama` |
| `CLAUDE_MODEL` | `claude-opus-5-5` | e.g. `claude-haiku-5-5` for lower cost |
| `OLLAMA_URL` / `OLLAMA_MODEL` | `http://ollama:11434` / `qwen2.5vl:7b` | |
| `LIBRARY_DIR` | `/library` | folder scanned for magazines |
| `OUTPUT_DIR` | *(empty)* | move renamed files here instead of renaming in place |
| `CONFIG_DIR` | `/config` | database, settings, cover cache |
| `FILENAME_TEMPLATE` | `{magazine} - {year}-{month:02}[ - Issue {issue}]` | |
| `TITLE_TEMPLATE` | `{magazine} - {month_name} {year}[ (Issue {issue})]` | Booklore title |
| `AUTHOR_TEMPLATE` | `{publisher}` | `;` separates several authors |
| `DESCRIPTION_TEMPLATE` | `[{headline}\n\n][In this issue: {cover_lines}]` | |
| `CATEGORIES` | `Magazine` | comma separated, always added |
| `WRITE_SIDECAR` / `WRITE_SIDECAR_COVER` | `true` | |
| `WRITE_PDF_METADATA` | `true` | embed title/author/description in PDFs |
| `SKIP_IF_SIDECAR_EXISTS` | `true` | ignore files that already have a sidecar |
| `AUTO_APPLY` / `MIN_CONFIDENCE_AUTO_APPLY` | `false` / `0.8` | |
| `SCAN_INTERVAL_MINUTES` | `0` | periodic scan + read (0 = manual) |
| `PORT` | `8484` | |

## Undo and safety

- Nothing is renamed or written until you press apply (unless you enabled auto-apply).
- **Undo rename** on an issue's page moves the file back and deletes the sidecar files magscan created. The embedded PDF metadata stays, but it is only descriptive and doesn't affect the content.
- PDF metadata is written with an incremental save. The original bytes stay as they are and the new metadata is appended.
- Covers go to the model you configured and nowhere else. With Ollama, nothing leaves your server.

## Development

```bash
pip install -r requirements.txt pytest httpx
python -m pytest -q tests
CONFIG_DIR=./config LIBRARY_DIR=./data python -m magscan serve
```
