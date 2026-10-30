# Card assets

## Fonts (bundled)

The card text uses the [Noto](https://notofonts.github.io/) fonts in `fonts/` (SIL Open Font License, see `fonts/OFL.txt`).
Each character is drawn with the first font that has it, so styled Discord names render properly:

| File | Covers |
|---|---|
| `NotoSans-Bold.ttf`, `NotoSans-Regular.ttf` | Latin, Greek, Cyrillic, small caps (ᴀʙᴄ), phonetic letters |
| `NotoSansCherokee-Bold.ttf`, `NotoSansCherokee-Regular.ttf` | Cherokee letters, often used as Latin look-alikes (ᎪᏴᏟ) |
| `NotoSansMath-Regular.ttf` | Styled letters (𝓪𝓫𝓬, 𝐚𝐛𝐜, 𝕒𝕓𝕔), ✓, ★ |
| `NotoSansSymbols2-Regular.ttf` | Symbols and dingbats (❤ ♡ ☆ …) |
| `NotoEmoji.ttf` | Emoji (drawn in one colour) |
| `NotoSansEgyptianHieroglyphs-Regular.ttf` | Egyptian hieroglyphs used as decoration (𓆩 𓆪 𓂀) |
| `NotoSansJavanese-Regular.ttf` | Javanese characters used as decoration (꧁ ꧂) |
| `NotoSansSymbols-Regular.ttf` | More symbols (☬ ⚜ …) |
| `NotoSerifTibetan-Regular.ttf` | Tibetan characters used as decoration (༒) |

Characters no font has fall back to a look-alike (Japanese `・` → `·`, `【】` → `[]`) or their plain form
where Unicode defines one (fullwidth Ｆ → F), otherwise a box. Each missing character is logged once
as a warning naming it, so you know which font to add.
Not covered: Chinese/Japanese/Korean, Arabic, Indic scripts. Add a Noto font for those and list it in
`FONT_CHAINS` in `bot/services/cards.py`.

To use your own font instead, add `fonts/bold.ttf` and/or `fonts/regular.ttf`; they are tried first.
Fonts are cached on first use, so restart the bot after changing them.

## Backgrounds (optional)

| File | Used for |
|---|---|
| `welcome/background.png` | Welcome card background (cropped to 1024×400). Default: blue gradient. |
| `goodbye/background.png` | Goodbye card background (cropped to 1024×400). Default: grey gradient. |
| `levels/background.png` | `/rank` card background (cropped to 934×282). Default: dark gradient. |

Backgrounds are read on every render, so no restart is needed.
