"""x_guard — pre-enqueue mechanical checks (V6 persona + content guard).

Crude regex-level net. Skill side (Gamma 関門) does semantic judgment;
this layer just catches obvious foot-guns.

Returns a list of (level, code, message) where level in {"error", "warn"}.
Caller decides whether to block ("error") or print and continue ("warn").
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

# ASCII = 1, else = 2 (approximation of X's weighted-char rule)
def weighted_len(text: str) -> int:
    return sum(1 if ord(c) < 0x0080 else 2 for c in text)


# --- person-name heuristics ---
# bare @handle inside text body (X UI auto-links these)
_AT_HANDLE = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]{2,15}")
# Japanese honorifics that imply naming an individual
_HONORIFIC_SUFFIX = re.compile(
    r"[一-龯々ぁ-んァ-ヶー゠-ヿ]{2,8}(?:さん|氏|先生|社長|教授|議員|大臣|博士)"
)

# --- URL detection ---
_URL = re.compile(r"https?://\S+")

# --- 軽口・賢者ぶり接頭 (粗い検出) ---
# 同一文末助詞「だね/だよ/さ/わ」が 2 回以上連続出現
_TRAILING_SOFT = re.compile(r"(?:だね|だよ|だわ|ですよ|ですね)")
# 文頭「確かに」「なるほど」「とはいえ」(賢者ぶり A 橋抜き / B 言葉拾い 候補)
_OPENING_SAGE = re.compile(r"^(?:確かに|なるほど|とはいえ|ただし|まあ|ね、)")


# --- 攻撃語彙 (memory gamma_formality_warning_attack_intensification 抜粋 4 軸) ---
# 思想自体じゃなく人物に向く語、または安全装置語
_ATTACK_VOCAB = {
    "destroy": re.compile(r"(?:潰す|破壊|終わらせる|消す|やめさせ|淘汰)"),
    "label": re.compile(r"(?:バカ|アホ|無能|無知|愚か|低脳|底辺)"),
    "soothing": re.compile(r"(?:大丈夫|安心して|気にしない|許される|そのままで)"),
    "trend": re.compile(r"(?:今こそ|本当に重要|これからの時代|時代遅れ|令和の)"),
}


def _jaccard_bigrams(a: str, b: str) -> float:
    def grams(s: str) -> set[str]:
        s = re.sub(r"\s+", "", s)
        return {s[i : i + 2] for i in range(len(s) - 1)} or {s}
    A, B = grams(a), grams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def check_text(
    text: str,
    *,
    mode: str = "post",
    cta_url: str | None = None,
    prior_texts: Iterable[tuple[str, datetime]] = (),
    now: datetime | None = None,
) -> list[tuple[str, str, str]]:
    """
    mode: "post" / "quote" / "reply"
    cta_url: configured philosophy-chat URL; if set, "post" requires it in text.
    prior_texts: iterable of (text, posted_at) for continuity check (7-day window).
    """
    out: list[tuple[str, str, str]] = []

    # G1 empty / too short
    t = text.strip()
    if not t:
        out.append(("error", "G1", "text is empty"))
        return out
    if len(t) < 5:
        out.append(("error", "G1", f"text too short ({len(t)} chars)"))

    # G2 length
    w = weighted_len(t)
    if w > 280:
        out.append(("error", "G2", f"weighted length {w}/280"))
    elif w > 240:
        out.append(("warn", "G2", f"weighted length {w}/280 (close to limit)"))

    # G3 攻撃語彙 hit 軸数
    hit_axes = [k for k, pat in _ATTACK_VOCAB.items() if pat.search(t)]
    if len(hit_axes) >= 2:
        out.append(("warn", "G3", f"attack-vocabulary axes hit: {hit_axes}"))

    # G4 人物名 (@handle or honorific-suffix)
    handles = _AT_HANDLE.findall(t)
    honor = _HONORIFIC_SUFFIX.findall(t)
    if handles:
        out.append(("warn", "G4", f"@handle in text: {handles} (quote_tweet_no_name_mention)"))
    if honor:
        out.append(("warn", "G4", f"honorific-suffix name: {honor} (思想 vs 人物境界)"))

    # G5 CTA: post should contain URL; quote should NOT contain URL
    has_url = bool(_URL.search(t))
    if mode == "post" and cta_url and (cta_url not in t):
        out.append(("warn", "G5", f"no philosophy-chat URL ({cta_url}) in post text"))
    if mode == "quote" and has_url:
        out.append(("warn", "G5", "quote-tweet text contains URL (引用元と重複)"))

    # G6 軽口連続 / 賢者ぶり 文頭
    soft_count = len(_TRAILING_SOFT.findall(t))
    if soft_count >= 2:
        out.append(("warn", "G6", f"soft endings × {soft_count} (刃が鈍る方向)"))
    if _OPENING_SAGE.match(t):
        out.append(("warn", "G6", "opening with sage-frame phrase (橋抜き / 言葉拾い 候補)"))

    # G7 continuity (Jaccard 2-gram > 0.4 within 7 days)
    now = now or datetime.now()
    cutoff = now - timedelta(days=7)
    for prior_text, posted_at in prior_texts:
        if posted_at < cutoff:
            continue
        sim = _jaccard_bigrams(t, prior_text)
        if sim >= 0.4:
            preview = prior_text if len(prior_text) <= 40 else prior_text[:40] + "..."
            out.append(
                (
                    "warn",
                    "G7",
                    f"similarity {sim:.2f} to recent post ({posted_at.strftime('%m-%d')}): {preview}",
                )
            )
            break  # one warning is enough

    return out


def render(report: list[tuple[str, str, str]]) -> str:
    if not report:
        return "  [guards] ok"
    lines = []
    for level, code, msg in report:
        prefix = "ERROR" if level == "error" else "warn"
        lines.append(f"  [{prefix}/{code}] {msg}")
    return "\n".join(lines)


def has_error(report: list[tuple[str, str, str]]) -> bool:
    return any(level == "error" for level, _, _ in report)
