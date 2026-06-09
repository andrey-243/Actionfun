"""
Email — construction HTML et envoi via Resend SDK.

send_digest_email(to, digests, lang, period) :
  - period : 'daily' | 'weekly'
  - lang   : 'en' | 'fr'
  - Sandbox : RESEND_FROM_EMAIL=onboarding@resend.dev (livraison uniquement vers l'adresse du compte).
  - Prod    : vérifier un domaine dans Resend dashboard et changer RESEND_FROM_EMAIL.
"""
import os
from datetime import date
import resend

SENTIMENT_EMOJI = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}

_FOOTER = {
    "en": "You receive this digest because you follow these stocks on StockVibe.",
    "fr": "Tu reçois ce digest car tu suis ces actions sur StockVibe.",
}
_UNSUBSCRIBE = {"en": "Unsubscribe", "fr": "Se désabonner"}
_PERIOD_LABEL = {
    "en": {"daily": "Daily Digest", "weekly": "Weekly Digest"},
    "fr": {"daily": "Digest du jour", "weekly": "Digest de la semaine"},
}


def _build_html(digests: list[dict], lang: str, period: str) -> str:
    label = _PERIOD_LABEL.get(lang, _PERIOD_LABEL["fr"]).get(period, period)
    today = date.today().strftime("%d/%m/%Y")

    sections = []
    for d in digests:
        sentiment = d.get("sentiment")
        sentiment_html = ""
        if sentiment:
            emoji = SENTIMENT_EMOJI.get(sentiment, "➡️")
            color_bg = '#1a3a2a' if sentiment == 'BULLISH' else '#3a1a1a' if sentiment == 'BEARISH' else '#2a2a2a'
            color_fg = '#4CAF82' if sentiment == 'BULLISH' else '#E05C4B' if sentiment == 'BEARISH' else '#9E9B95'
            sentiment_html = f'<span style="padding:2px 10px;border-radius:4px;font-size:11px;font-weight:700;font-family:monospace;background:{color_bg};color:{color_fg};">{emoji} {sentiment}</span>'

        articles_html = "".join(
            f'<li><a href="{a["url"]}" style="color:#4CAF82;text-decoration:none;">{a["headline"]}</a> <span style="color:#9E9B95;font-size:12px;">— {a["source"]}</span></li>'
            for a in d.get("articles", [])
        )
        sections.append(f"""
<div style="margin-bottom:32px;padding:20px;background:#1E1D1B;border-radius:8px;border:1px solid #302F2C;">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
    <span style="font-family:monospace;font-size:18px;font-weight:800;color:#E8E5DF;">{d['ticker']}</span>
    {sentiment_html}
  </div>
  <p style="color:#E8E5DF;font-size:14px;line-height:1.7;margin:0 0 16px;">{d['summary']}</p>
  <ul style="color:#C8C5BF;font-size:13px;line-height:1.8;padding-left:18px;margin:0;">{articles_html}</ul>
</div>""")

    footer_text = _FOOTER.get(lang, _FOOTER["fr"])
    unsubscribe = _UNSUBSCRIBE.get(lang, _UNSUBSCRIBE["fr"])

    return f"""<!DOCTYPE html>
<html>
<body style="background:#141412;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:32px 16px;">
  <div style="max-width:600px;margin:0 auto;">
    <div style="margin-bottom:28px;">
      <span style="font-family:monospace;font-weight:800;font-size:20px;color:#E8E5DF;">StockVibe</span>
      <span style="margin-left:8px;font-size:12px;color:#9E9B95;">{label} · {today}</span>
    </div>
    {''.join(sections)}
    <p style="color:#5C5A56;font-size:11px;margin-top:24px;">
      {footer_text}<br>
      <a href="#" style="color:#5C5A56;">{unsubscribe}</a>
    </p>
  </div>
</body>
</html>"""


def send_digest_email(to_email: str, digests: list[dict], lang: str = "fr", period: str = "daily") -> None:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        return

    resend.api_key = api_key
    tickers = ", ".join(d["ticker"] for d in digests)
    label = _PERIOD_LABEL.get(lang, _PERIOD_LABEL["fr"]).get(period, period)

    resend.Emails.send({
        "from": os.environ.get("RESEND_FROM_EMAIL", "digest@stockvibe.app"),
        "to": [to_email],
        "subject": f"StockVibe · {label} {date.today().strftime('%d/%m/%Y')} — {tickers}",
        "html": _build_html(digests, lang=lang, period=period),
    })
