"""
ContextualIdentityEngine — توليد هوية سياقية متكاملة ومتوافقة.
الهوية تتوافق مع: بلد البروكسي + بلد إصدار البطاقة (BIN).
تضارب الهوية مع الـ IP هو أحد أقوى إشارات الاحتيال لدى Stripe/Braintree.

يُولّد: اسم + عنوان + ZIP code + رقم هاتف متوافقة مع البلد المستهدف.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class CardholderIdentity:
    first_name: str
    last_name: str
    full_name: str
    address_line1: str
    city: str
    state: str
    zip_code: str
    country_code: str
    phone: str


_COUNTRY_DATA: Dict[str, dict] = {
    "US": {
        "first_names": ["James","John","Michael","Robert","David","William","Joseph","Charles","Thomas","Christopher",
                        "Mary","Patricia","Jennifer","Linda","Barbara","Elizabeth","Susan","Jessica","Sarah","Karen"],
        "last_names": ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Wilson","Martinez"],
        "cities": [("New York", "NY", "10001"), ("Los Angeles", "CA", "90001"), ("Chicago", "IL", "60601"),
                   ("Houston", "TX", "77001"), ("Phoenix", "AZ", "85001"), ("Philadelphia", "PA", "19101"),
                   ("San Antonio", "TX", "78201"), ("San Diego", "CA", "92101"), ("Dallas", "TX", "75201"),
                   ("Austin", "TX", "78701")],
        "streets": ["Main St", "Oak Ave", "Maple Dr", "Cedar Ln", "Pine St", "Elm St", "Washington Blvd", "Park Ave"],
        "phone_prefix": "+1",
        "phone_format": lambda: f"+1{random.randint(200,999)}{random.randint(100,999)}{random.randint(1000,9999)}",
    },
    "GB": {
        "first_names": ["Oliver","Harry","George","Jack","Noah","Charlie","James","William","Thomas","Henry",
                        "Olivia","Emma","Isla","Ava","Mia","Sophia","Isabella","Grace","Amelia","Lily"],
        "last_names": ["Smith","Jones","Williams","Taylor","Brown","Davies","Evans","Wilson","Thomas","Roberts"],
        "cities": [("London", "ENG", "EC1A 1BB"), ("Manchester", "ENG", "M1 1AE"), ("Birmingham", "ENG", "B1 1BB"),
                   ("Leeds", "ENG", "LS1 1BA"), ("Glasgow", "SCO", "G1 1AA"), ("Edinburgh", "SCO", "EH1 1AA")],
        "streets": ["High Street", "Church Road", "Victoria Road", "Park Lane", "Queens Road", "King Street"],
        "phone_format": lambda: f"+44{random.randint(7000000000, 7999999999)}",
    },
    "DE": {
        "first_names": ["Lukas","Jonas","Leon","Finn","Noah","Maximilian","Elias","Ben","Luca","Tim",
                        "Emma","Hannah","Mia","Sophia","Lena","Lea","Laura","Julia","Anna","Sarah"],
        "last_names": ["Müller","Schmidt","Schneider","Fischer","Weber","Meyer","Wagner","Becker","Schulz","Hoffmann"],
        "cities": [("Berlin", "BE", "10115"), ("Hamburg", "HH", "20095"), ("Munich", "BY", "80331"),
                   ("Cologne", "NW", "50667"), ("Frankfurt", "HE", "60311")],
        "streets": ["Hauptstraße", "Gartenstraße", "Bahnhofstraße", "Dorfstraße", "Schulstraße"],
        "phone_format": lambda: f"+49{random.randint(15000000000, 17999999999)}",
    },
    "FR": {
        "first_names": ["Lucas","Hugo","Louis","Gabriel","Raphaël","Léo","Arthur","Nathan","Tom","Paul",
                        "Emma","Jade","Louise","Alice","Chloé","Lina","Léa","Manon","Inès","Sarah"],
        "last_names": ["Martin","Bernard","Thomas","Petit","Robert","Richard","Durand","Dubois","Moreau","Simon"],
        "cities": [("Paris", "IDF", "75001"), ("Lyon", "ARA", "69001"), ("Marseille", "PAC", "13001"),
                   ("Toulouse", "OCC", "31000"), ("Bordeaux", "NAQ", "33000")],
        "streets": ["Rue de la Paix", "Avenue des Fleurs", "Boulevard Voltaire", "Rue du Commerce"],
        "phone_format": lambda: f"+33{random.randint(600000000, 699999999)}",
    },
    "SA": {
        "first_names": ["Mohammed","Abdullah","Ahmed","Ali","Omar","Khalid","Faisal","Abdulrahman","Hamad","Saud",
                        "Fatima","Sara","Nora","Hessa","Dana","Lina","Maha","Reem","Hala","Noura"],
        "last_names": ["Al-Saud","Al-Rashid","Al-Otaibi","Al-Ghamdi","Al-Zahrani","Al-Qahtani","Al-Harbi","Al-Shehri"],
        "cities": [("Riyadh", "RU", "11564"), ("Jeddah", "MK", "21589"), ("Mecca", "MK", "24231"),
                   ("Medina", "MD", "42311"), ("Dammam", "EP", "32411")],
        "streets": ["King Fahd Road", "Prince Sultan Street", "Al-Madinah Road", "Tahlia Street"],
        "phone_format": lambda: f"+966{random.randint(500000000, 599999999)}",
    },
    "AE": {
        "first_names": ["Mohammed","Ahmed","Ali","Omar","Khalid","Rashid","Hamdan","Zayed","Maktoum","Saeed",
                        "Fatima","Mariam","Aisha","Sara","Hessa","Latifa","Noura","Maryam","Dana","Shaikha"],
        "last_names": ["Al-Maktoum","Al-Nahyan","Al-Falasi","Al-Mazrouei","Al-Nuaimi","Al-Rashidi"],
        "cities": [("Dubai", "DU", "00000"), ("Abu Dhabi", "AZ", "00000"), ("Sharjah", "SH", "00000")],
        "streets": ["Sheikh Zayed Road", "Al Wasl Road", "Jumeirah Beach Road", "Business Bay"],
        "phone_format": lambda: f"+971{random.randint(500000000, 599999999)}",
    },
    "TR": {
        "first_names": ["Ahmet","Mehmet","Mustafa","Ali","Hüseyin","Hasan","İbrahim","Yusuf","Murat","Ömer",
                        "Fatma","Ayşe","Emine","Hatice","Zeynep","Elif","Meryem","Şerife","Hanife","Rukiye"],
        "last_names": ["Yılmaz","Kaya","Demir","Şahin","Çelik","Yıldız","Yıldırım","Öztürk","Aydın","Özdemir"],
        "cities": [("Istanbul", "34", "34000"), ("Ankara", "06", "06000"), ("Izmir", "35", "35000")],
        "streets": ["Atatürk Caddesi", "İstiklal Caddesi", "Bağdat Caddesi", "Cumhuriyet Bulvarı"],
        "phone_format": lambda: f"+90{random.randint(5000000000, 5999999999)}",
    },
}

_FALLBACK_COUNTRY = "US"


class ContextualIdentityEngine:
    """
    يُولّد هوية متوافقة مع بلد البروكسي أو BIN.
    يمنع anomaly ناتج عن تضارب الاسم/العنوان مع الـ IP.
    """

    def generate(
        self,
        proxy_country: str = "US",
        bin_country: Optional[str] = None,
    ) -> CardholderIdentity:
        target = (bin_country or proxy_country or _FALLBACK_COUNTRY).upper()
        data = _COUNTRY_DATA.get(target, _COUNTRY_DATA[_FALLBACK_COUNTRY])

        first = random.choice(data["first_names"])
        last = random.choice(data["last_names"])
        city_info = random.choice(data["cities"])
        city, state, zip_code = city_info if len(city_info) == 3 else (city_info[0], "", city_info[1])
        street_num = random.randint(10, 9999)
        street = random.choice(data["streets"])
        address = f"{street_num} {street}"
        phone = data["phone_format"]()

        identity = CardholderIdentity(
            first_name=first,
            last_name=last,
            full_name=f"{first} {last}",
            address_line1=address,
            city=city,
            state=state,
            zip_code=zip_code,
            country_code=target,
            phone=phone,
        )
        log.debug(
            "IdentityEngine: generated %s / %s / %s for country=%s",
            identity.full_name, city, zip_code, target,
        )
        return identity


identity_engine = ContextualIdentityEngine()
