from gunicorn.glogging import Logger


def safe_access_atoms(atoms,environ):
    path=environ.get('PATH_INFO','')
    if path.startswith('/accounts/reset/'):
        path='/accounts/reset/[redacted]/'
    atoms['U']=path
    atoms['q']=''
    atoms['r']=f"{environ.get('REQUEST_METHOD','')} {path} {environ.get('SERVER_PROTOCOL','')}"
    return atoms


class RedactedAccessLogger(Logger):
    def atoms(self,response,request,environ,request_time):
        return safe_access_atoms(super().atoms(response,request,environ,request_time),environ)
