"""Validated, project-owned website settings, separated by investigation type."""
from .forms import ScanForm


def defaults_for(target,kind):
    values={name:field.initial for name,field in ScanForm.base_fields.items() if field.initial is not None and name!='kind'}
    values['url']=''
    values.update((getattr(target,'scan_settings',{}) or {}).get(kind,{}))
    values['check_external_links']=kind=='crawl'
    return values
