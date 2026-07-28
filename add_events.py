"""Add supply events manually — from a lineup screenshot or JSON.

Usage:
  uv run python add_events.py space_september.png        # parse screenshot with Claude
  uv run python add_events.py --json '[{"date":"2026-09-12","name":"Joseph Capriati",
        "venue":"Club Space Miami","artists":["Joseph Capriati"]}]'
  Add --yes to skip the confirmation prompt.

Screenshot parsing needs ANTHROPIC_API_KEY in .env (or an `ant auth login`
profile). Events land in manual_events.json (committed, picked up by the
daily pipeline) and in Supabase when SUPABASE_URL/SUPABASE_SERVICE_KEY are set.
"""

import base64
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from scene_radar import manual

MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}

SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                    "name": {"type": "string", "description": "Event title; if none, join the artist names"},
                    "venue": {"type": "string"},
                    "artists": {"type": "array", "items": {"type": "string"}},
                    "price": {"type": ["string", "null"]},
                    "genres": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["date", "name", "venue", "artists", "price", "genres"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}


def parse_screenshot(path: Path) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if not media_type:
        raise SystemExit(f"Unsupported image type: {path.suffix}")
    image_data = base64.standard_b64encode(path.read_bytes()).decode()

    response = client.beta.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": (
                    f"This is a nightclub event announcement (probably a Miami venue's "
                    f"Instagram post). Today is {date.today().isoformat()}. Extract every "
                    f"event as structured data. Dates without a year mean the next future "
                    f"occurrence. The lineup artists are usually the headline text; split "
                    f"b2b/x/+/& into separate artists. If the venue isn't visible in the "
                    f"image, use the poster's branding to infer it; leave price null "
                    f"unless shown."
                )},
            ],
        }],
    )
    if response.stop_reason == "refusal":
        raise SystemExit("Claude declined to parse this image — add the events via --json instead.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["events"]


def main() -> None:
    load_dotenv()
    args = [a for a in sys.argv[1:] if a != "--yes"]
    assume_yes = "--yes" in sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)

    if args[0] == "--json":
        payload = args[1] if len(args) > 1 else sys.stdin.read()
        new_events = json.loads(payload)
        if isinstance(new_events, dict):
            new_events = new_events.get("events", [new_events])
    else:
        path = Path(args[0])
        if not path.exists():
            raise SystemExit(f"No such file: {path}")
        print(f"Parsing {path.name} with Claude…")
        new_events = parse_screenshot(path)

    if not new_events:
        raise SystemExit("No events found.")

    print(f"\n{len(new_events)} event(s):")
    for e in new_events:
        artists = ", ".join(e.get("artists", [])) or "—"
        print(f"  {e['date']}  {e['name']:<45.45} @ {e.get('venue', '?'):<22.22} [{artists}]")

    if not assume_yes and input("\nAdd these? [y/N] ").strip().lower() != "y":
        raise SystemExit("Aborted.")

    existing = manual.load_file_events()
    known = {manual._event_id(e) for e in existing}
    added = [e for e in new_events if manual._event_id(e) not in known]
    manual.save_file_events(existing + added)
    print(f"Added {len(added)} to manual_events.json ({len(new_events) - len(added)} already present).")

    if manual.supabase_configured():
        ok = manual.push_supabase_events(existing + added)
        print("Synced to Supabase." if ok else "Supabase sync FAILED (file copy saved).")
    print("They'll enter the radar on the next pipeline run (or run: uv run python run_all.py).")


if __name__ == "__main__":
    main()
