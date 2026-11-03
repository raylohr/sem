"""System-resolver DNS and public RIPEstat enrichment, independently budgeted."""
import ipaddress
import json
from scans.network import normalize_host, public_ip, SafeClient


def observe_domain(hostname, check_access, *, ripe=True):
    import dns.resolver
    hostname = normalize_host(hostname)
    resolver = dns.resolver.Resolver()
    resolver.timeout = 1
    resolver.lifetime = 2
    records, coverage = {}, {}
    for kind in ('A','AAAA','CNAME','MX','TXT','NS'):
        check_access()
        try:
            answer = resolver.resolve(hostname, kind, lifetime=2, raise_on_no_answer=False)
            records[kind] = {'values': [item.to_text()[:4096] for item in answer][:30], 'ttl': answer.rrset.ttl if answer.rrset else None}
        except dns.resolver.NoAnswer:
            records[kind] = {'values': [], 'ttl': None}
        except Exception as exc:
            coverage[kind] = type(exc).__name__
    if not records.get('NS',{}).get('values'):
        try:
            check_access()
            zone = dns.resolver.zone_for_name(hostname, resolver=resolver, lifetime=2).to_text()
            answer = resolver.resolve(zone,'NS',lifetime=2)
            records['NS'] = {'values':[item.to_text() for item in answer][:30], 'ttl':answer.rrset.ttl, 'zone':zone}
        except Exception as exc:
            coverage['NS_zone'] = type(exc).__name__
    addresses = records.get('A',{}).get('values',[]) + records.get('AAAA',{}).get('values',[])
    public_addresses = [address for address in addresses if public_ip(address)]
    network = {'public_addresses': public_addresses[:4], 'non_public_answer': len(public_addresses)!=len(addresses), 'physical_location': 'unknown', 'origin_hosting_behind_cdn': 'unknown'}
    if ripe:
        networks=[]
        client=SafeClient(['stat.ripe.net'],check_access=check_access,duration=15,max_transfer=1024*1024,delay=0.5,respect_robots=False)
        try:
            for address in public_addresses[:2]:
                check_access()
                result=client.fetch('https://stat.ripe.net/data/network-info/data.json?resource='+str(ipaddress.ip_address(address)),enforce_robots=False)
                if result.status==200:
                    payload=json.loads(result.body).get('data',{})
                    networks.append({'address':address,'asns':[str(value) for value in payload.get('asns',[])[:5]],'prefix':payload.get('prefix'),'source':'https://stat.ripe.net/docs/data-api/api-endpoints/network-info'})
            network['networks']=networks
        except Exception as exc:
            coverage['asn']=type(exc).__name__
        finally:
            client.close()
    return {'records':records,'network':network,'coverage':coverage}
