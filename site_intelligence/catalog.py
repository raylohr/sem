"""Versioned local knowledge; seed imports never overwrite reviewed suppliers."""
import datetime
import json
from pathlib import Path

EU27 = frozenset('AT BE BG HR CY CZ DE DK EE ES FI FR GR HU IE IT LT LU LV MT NL PL PT RO SE SI SK'.split())
ISO_CODES = frozenset('AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'.split())


def supplier_snapshot(supplier):
    return {key: getattr(supplier, key) for key in ('key','name','country','parent_name','parent_country','revision','sources')} | {'checked_at': supplier.checked_at.isoformat() if supplier.checked_at else None, 'role': 'publisher; not automatically contracting processor'}


def install_catalog(apps=None):
    if apps is None:
        from django.apps import apps
    Supplier = apps.get_model('site_intelligence','Supplier')
    Revision = apps.get_model('site_intelligence','SupplierRevision')
    Technology = apps.get_model('site_intelligence','Technology')
    Relation = apps.get_model('site_intelligence','TechnologySupplier')
    Rule = apps.get_model('site_intelligence','DetectionRule')
    data = json.loads((Path(__file__).parent/'data/catalog-v1.json').read_text())
    suppliers = {}
    for item in data['suppliers']:
        values = {key:value for key,value in item.items() if key != 'key'}
        if values.get('checked_at'):
            values['checked_at'] = datetime.date.fromisoformat(values['checked_at'])
        supplier, created = Supplier.objects.get_or_create(key=item['key'], defaults=values)
        suppliers[item['key']] = supplier
        if created:
            Revision.objects.create(supplier=supplier,revision=1,snapshot={**item,'role':'publisher; not automatically contracting processor'})
    for item in data['technologies']:
        technology, _ = Technology.objects.get_or_create(key=item['key'], defaults={key:item[key] for key in ('name','category','website','aliases')})
        if item.get('supplier'):
            Relation.objects.get_or_create(technology=technology,supplier=suppliers[item['supplier']],role='publisher',valid_from=datetime.date(2026,11,3), defaults={'source_url': item['website']})
        for number, rule in enumerate(item['rules']):
            Rule.objects.get_or_create(key=f'{item["key"]}:{number}',version='catalog-v1',defaults={'technology':technology,**rule,'source_url':item['website']})
