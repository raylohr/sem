"""Immutable report-v1 scoring definitions. Unknown controls never earn points."""
from assessments.advanced import RULES

VERSION='report-v1'
CATEGORIES={'technical':'Techniek & indexeerbaarheid','content':'Content & structuur',
    'experience':'Gebruikerservaring & toegankelijkheid','privacy':'Privacy & toestemming','technology':'Technologie & afhankelijkheid'}
BASE={'links_to_404':'link_to_404','titles':'missing_titles','meta_descriptions':'missing_meta_descriptions','h1':'missing_h1'}
CONTENT={'missing_titles','missing_meta_descriptions','missing_h1','multiple_titles','multiple_meta_descriptions','multiple_h1',
    'duplicate_titles','duplicate_meta_descriptions','duplicate_text','language_missing','heading_jump','no_observed_inlinks'}
EXPERIENCE={'image_alt_missing','mixed_content','broken_resource','a11y_contrast','a11y_alt','a11y_structure','performance_field'}
TECHNICAL=set(RULES)-CONTENT-EXPERIENCE|{'link_to_404'}
EXPECTED={'technical':sorted(TECHNICAL),'content':sorted(CONTENT),'experience':sorted(EXPERIENCE)}
STATES={'issue':3,'unknown':2,'pass':1,'not_applicable':0}


def category(rule):
    if rule.startswith(('tracking_','policy_')):return 'privacy'
    if rule.startswith('accessibility_') or rule in EXPERIENCE:return 'experience'
    if rule in CONTENT:return 'content'
    return 'technical'


def weight(rule):
    return 3 if rule in {'http_error','redirect_loop','canonical_invalid','canonical_multiple','canonical_target','canonical_loop',
        'hreflang_target','link_to_404','link_to_5xx','mixed_content'} else 1


def normalized_checks(checks):
    return {BASE.get(key,key):value for key,value in checks.items() if value in STATES}


def score(checks_by_url,rules):
    counts={'pass':0,'issue':0,'unknown':0,'not_applicable':0};earned=possible=0
    per_rule={rule:{'pass':0,'issue':0,'unknown':0,'not_applicable':0,'weight':weight(rule)} for rule in rules}
    for checks in checks_by_url:
        for rule in rules:
            state=checks.get(rule,'unknown');state=state if state in STATES else 'unknown'
            counts[state]+=1;per_rule[rule][state]+=1
            if state!='not_applicable':
                possible+=weight(rule)
                if state=='pass':earned+=weight(rule)
    applicable=sum(counts[s] for s in ('pass','issue','unknown'))
    known=counts['pass']+counts['issue']
    coverage=round(100*known/applicable,1) if applicable else 0
    state='not_applicable' if counts['not_applicable'] and not applicable else 'insufficient' if not applicable or known*100<applicable*80 else 'measured'
    value=round(100*earned/possible) if state=='measured' and possible else None
    return state,value,{'counts':counts,'known':known,'applicable':applicable,'percent':coverage}, {'version':VERSION,
        'rules':{r:weight(r) for r in rules},'per_rule':per_rule,'earned_weight':earned,'possible_weight':possible,
        'threshold_percent':80,'formula':'100 * weighted passing URL-controls / weighted applicable URL-controls; unknown earns zero; N/A excluded'}
