"""M6 — конвейер снятия: справочник площадок, шаблоны жалоб, отправка (Postmark / Mailgun)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ..config import settings
from .base import NotConfigured, client, log_call
from .search import domain_of

PLATFORMS_FILE = Path(__file__).resolve().parent.parent / "data" / "platforms.json"


def load_platforms() -> dict:
    data = json.loads(PLATFORMS_FILE.read_text(encoding="utf-8"))
    return data["platforms"]


def save_platforms(platforms: dict):
    data = json.loads(PLATFORMS_FILE.read_text(encoding="utf-8"))
    data["platforms"] = platforms
    PLATFORMS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_platform(url: str) -> tuple[str, dict]:
    platforms = load_platforms()
    dom = domain_of(url)
    parts = dom.split(".")
    for i in range(len(parts) - 1):
        key = ".".join(parts[i:])
        if key in platforms:
            p = platforms[key]
            if "alias" in p:
                return p["alias"], platforms[p["alias"]]
            return key, p
    return dom or "*", platforms["*"]


# --- шаблоны (RU + EN в одном письме: площадки читают английский, СНГ-хостеры — русский) ---

TEMPLATES = {
    "dmca": {
        "subject": "DMCA Takedown Notice — unauthorized copyrighted images at {url}",
        "body": """To whom it may concern,

I am writing on behalf of the copyright owner of photographs that appear without authorization at:
{url}

Original works: photographs created by and depicting our client; proof of authorship (originals with EXIF data, hash values and dated archive copies) is available on request. Evidence package reference: {evidence_ref}.

I have a good-faith belief that the use of the material is not authorized by the copyright owner, its agent, or the law. The information in this notice is accurate, and under penalty of perjury, I am authorized to act on behalf of the owner of the exclusive rights.

Please remove or disable access to the material within 72 hours and confirm by replying to this email.

Signed: PRIVÉ Digital Privacy Services, authorized agent
Contact: {reply_to}

---
Уведомление о нарушении авторских прав. По адресу выше размещены фотографии без согласия правообладателя. Просим удалить материал в течение 72 часов и подтвердить ответным письмом.
""",
    },
    "ncii": {
        "subject": "Non-consensual intimate imagery — urgent removal request: {url}",
        "body": """Hello,

The content at the following address contains intimate images of our client, published without her consent:
{url}

This violates your policy on non-consensual intimate imagery and applicable law. We request immediate removal of the content and of any copies. Hashes of the images have been registered with StopNCII (case {stopncii_case}).

Evidence package reference: {evidence_ref}. Please confirm removal to {reply_to}.

PRIVÉ Digital Privacy Services, authorized agent of the depicted person

---
По указанному адресу размещены интимные изображения нашей клиентки без её согласия. Просим немедленно удалить материал и подтвердить удаление.
""",
    },
    "impersonation": {
        "subject": "Impersonation / fake profile using our client's photographs: {url}",
        "body": """Hello,

The profile at {url} uses photographs of our client without her consent and impersonates her. She has not created or authorized this account. Proof of identity and authorship of the photographs is available through our evidence package {evidence_ref}.

Please remove the profile and confirm to {reply_to}.

PRIVÉ Digital Privacy Services, authorized agent

---
Профиль по ссылке использует фотографии нашей клиентки без согласия и выдаёт себя за неё. Просим удалить профиль и подтвердить.
""",
    },
    "consent_withdrawal": {
        "subject": "Withdrawal of consent to publication — removal request: {url}",
        "body": """Hello,

Our client's photographs are published at {url}. Any consent to publication previously given (including through a former manager or agency) is withdrawn as of {today}. The copyright in the photographs belongs to our client.

Please remove the listing within 72 hours and confirm to {reply_to}. Evidence package reference: {evidence_ref}.

PRIVÉ Digital Privacy Services, authorized agent

---
Согласие на размещение фотографий, ранее данное (в том числе через бывшего менеджера или агентство), отозвано с {today}. Просим удалить анкету в течение 72 часов.
""",
    },
    "search_removal": {
        "subject": "Request to remove search results: {url}",
        "body": """Заявка на удаление из поисковой выдачи. URL: {url}. Основание: персональные данные и изображения размещены без согласия; исходная страница {status_note}. Пакет доказательств: {evidence_ref}.

Заполняется оператором через веб-форму поисковика (см. form_url). Текст выше — для поля «описание».
""",
    },
}


# Локализованные версии DMCA/NCII: площадки ЕС и Турции заметно лучше реагируют на родной язык.
LOCALIZED = {
    "de": {
        "dmca": ("Urheberrechtsverletzung – Aufforderung zur Entfernung: {url}",
                 """Sehr geehrte Damen und Herren,

unter {url} werden Fotografien ohne Zustimmung der Urheberin veröffentlicht. Wir handeln als bevollmächtigte Vertreter der Rechteinhaberin. Nachweise der Urheberschaft (Originaldateien mit EXIF-Daten, Hashwerte, datierte Archivkopien) liegen vor; Referenz: {evidence_ref}.

Wir fordern Sie auf, das Material innerhalb von 72 Stunden zu entfernen und die Löschung an {reply_to} zu bestätigen. Andernfalls behalten wir uns rechtliche Schritte vor (§ 97 UrhG, Art. 17 DSGVO).

Mit freundlichen Grüßen
PRIVÉ Digital Privacy Services
"""),
        "ncii": ("Dringend: nicht einvernehmliche Intimaufnahmen – sofortige Entfernung: {url}",
                 """Sehr geehrte Damen und Herren,

unter {url} werden intime Aufnahmen unserer Mandantin ohne ihre Einwilligung verbreitet. Dies verstößt gegen § 201a StGB und Ihre Nutzungsbedingungen. Die Hashwerte sind bei StopNCII registriert (Fall {stopncii_case}). Referenz: {evidence_ref}.

Wir fordern die sofortige Entfernung aller Kopien und eine Bestätigung an {reply_to}.

PRIVÉ Digital Privacy Services, bevollmächtigte Vertreter
"""),
    },
    "it": {
        "dmca": ("Violazione del diritto d'autore – richiesta di rimozione: {url}",
                 """Gentili Signori,

all'indirizzo {url} sono pubblicate fotografie senza il consenso dell'autrice e titolare dei diritti, di cui siamo rappresentanti autorizzati. Le prove di paternità (file originali con dati EXIF, hash, copie archiviate con data) sono disponibili; riferimento: {evidence_ref}.

Vi chiediamo di rimuovere il materiale entro 72 ore e di confermare la rimozione a {reply_to}. In mancanza, ci riserviamo di agire ai sensi della L. 633/1941 e del GDPR (art. 17).

Cordiali saluti
PRIVÉ Digital Privacy Services
"""),
        "ncii": ("Urgente: immagini intime non consensuali – rimozione immediata: {url}",
                 """Gentili Signori,

all'indirizzo {url} sono diffuse immagini intime della nostra assistita senza il suo consenso (art. 612-ter c.p.). Gli hash sono registrati presso StopNCII (caso {stopncii_case}). Riferimento: {evidence_ref}.

Chiediamo la rimozione immediata di tutte le copie e conferma a {reply_to}.

PRIVÉ Digital Privacy Services, rappresentanti autorizzati
"""),
    },
    "tr": {
        "dmca": ("Telif hakkı ihlali – kaldırma talebi: {url}",
                 """Sayın Yetkili,

{url} adresinde, hak sahibinin izni olmadan fotoğraflar yayımlanmaktadır. Hak sahibinin yetkili temsilcisi olarak başvuruyoruz. Eser sahipliğine ilişkin deliller (EXIF verili orijinal dosyalar, hash değerleri, tarihli arşiv kopyaları) mevcuttur; referans: {evidence_ref}.

5846 sayılı FSEK ve 5651 sayılı Kanun kapsamında içeriğin 72 saat içinde kaldırılmasını ve {reply_to} adresine teyit gönderilmesini talep ederiz.

Saygılarımızla
PRIVÉ Digital Privacy Services
"""),
        "ncii": ("Acil: rıza dışı mahrem görüntüler – derhal kaldırma: {url}",
                 """Sayın Yetkili,

{url} adresinde müvekkilimize ait mahrem görüntüler rızası dışında yayımlanmaktadır (TCK m. 134, 5651 sayılı Kanun m. 9/A). Hash değerleri StopNCII'de kayıtlıdır (dosya {stopncii_case}). Referans: {evidence_ref}.

Tüm kopyaların derhal kaldırılmasını ve {reply_to} adresine teyit talep ederiz.

PRIVÉ Digital Privacy Services, yetkili temsilci
"""),
    },
}

TLD_LANG = {"de": "de", "at": "de", "ch": "de", "it": "it", "tr": "tr", "com.tr": "tr"}


def lang_for(domain: str, platform: dict | None = None) -> str:
    if platform and platform.get("lang"):
        return platform["lang"]
    for tld, lang in TLD_LANG.items():
        if domain.endswith("." + tld):
            return lang
    return "en"


def render(template: str, finding, extra: dict | None = None, lang: str = "en") -> tuple[str, str]:
    t = TEMPLATES[template]
    if lang != "en" and template in LOCALIZED.get(lang, {}):
        subj, body = LOCALIZED[lang][template]
        t = {"subject": subj, "body": body + "\n---\n" + TEMPLATES[template]["body"].split("---")[0].strip()}
    ev = finding.evidence[-1] if finding.evidence else None
    ctx = {
        "url": finding.url,
        "evidence_ref": f"PRV-{finding.order_id}-{finding.id}" + (f" sha256:{ev.sha256[:16]}" if ev and ev.sha256 else ""),
        "reply_to": settings.mail_reply_to or settings.mail_from,
        "stopncii_case": (extra or {}).get("stopncii_case", "n/a"),
        "today": datetime.utcnow().strftime("%Y-%m-%d"),
        "status_note": (extra or {}).get("status_note", "already removed / удалена"),
    }
    return t["subject"].format(**ctx), t["body"].format(**ctx)


def send_email(to: str, subject: str, body: str, tag: str = "takedown") -> str:
    """Отправка через Postmark или Mailgun. Возвращает message id."""
    if settings.mail_provider == "postmark" and settings.postmark_token:
        with client() as c:
            r = c.post("https://api.postmarkapp.com/email",
                       headers={"X-Postmark-Server-Token": settings.postmark_token, "Accept": "application/json"},
                       json={"From": settings.mail_from, "To": to, "Subject": subject, "TextBody": body,
                             "ReplyTo": settings.mail_reply_to or settings.mail_from, "Tag": tag, "MessageStream": "outbound"})
            r.raise_for_status()
            mid = r.json().get("MessageID", "")
        log_call("postmark", f"send {to}", True, mid)
        return mid
    if settings.mail_provider == "mailgun" and settings.mailgun_api_key and settings.mailgun_domain:
        with client() as c:
            r = c.post(f"https://api.mailgun.net/v3/{settings.mailgun_domain}/messages",
                       auth=("api", settings.mailgun_api_key),
                       data={"from": settings.mail_from, "to": to, "subject": subject, "text": body,
                             "h:Reply-To": settings.mail_reply_to or settings.mail_from, "o:tag": tag})
            r.raise_for_status()
            mid = r.json().get("id", "")
        log_call("mailgun", f"send {to}", True, mid)
        return mid
    raise NotConfigured("mail")


def next_check() -> datetime:
    return datetime.utcnow() + timedelta(days=settings.takedown_recheck_days)


def status() -> dict:
    return {"mail_provider": settings.mail_provider or None,
            "mail_configured": bool((settings.mail_provider == "postmark" and settings.postmark_token)
                                    or (settings.mail_provider == "mailgun" and settings.mailgun_api_key)),
            "platforms": len(load_platforms()) - 1}
