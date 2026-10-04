"""Provider-neutral multilingual and regional intelligence primitives."""

from dataclasses import dataclass


RTL_LANGUAGES = {"ar", "arc", "dv", "fa", "he", "ku", "ps", "sd", "ug", "ur", "yi"}


@dataclass(frozen=True)
class LocaleContext:
    tag: str
    language: str
    script: str | None
    region: str | None
    rtl: bool


def resolve_locale(tag: str | None, fallback: str = "en") -> LocaleContext:
    raw = (tag or fallback).replace("_", "-").strip()
    parts = [part for part in raw.split("-") if part]
    language = parts[0].lower() if parts else fallback
    script = None
    region = None
    for part in parts[1:]:
        if script is None and len(part) == 4 and part.isalpha():
            script = part.title()
        elif region is None and ((len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit())):
            region = part.upper()
    canonical = language
    if script:
        canonical += "-" + script
    if region:
        canonical += "-" + region
    return LocaleContext(canonical, language, script, region, language in RTL_LANGUAGES)
