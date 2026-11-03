from itertools import islice
from django.db import transaction
from django.db.models import Subquery, OuterRef
from projects.models import Project
from sources.models import ImportBatch
from .models import ProjectURL, URLObservation
from .adapters import adapt_record,first,URL_COLUMNS


def prune_urls(project_id):
    # A scalar LIMIT-1 subquery keeps this as a parameterized FK-index probe.
    # LEFT JOIN/NOT EXISTS can become quadratic without statistics on a fresh
    # 100k-URL project because PostgreSQL may choose an unparameterized join scan.
    first_observation = URLObservation.objects.filter(url_id=OuterRef('pk')).values('pk')[:1]
    return ProjectURL.objects.filter(project_id=project_id).alias(
        first_observation=Subquery(first_observation)).filter(first_observation__isnull=True, label_assignments__isnull=True).delete()


@transaction.atomic
def index_batch(batch_id, *, derive_assessment=True, recover_documents=False):
    project_id = ImportBatch.objects.values_list("project_id", flat=True).get(pk=batch_id)
    Project.objects.select_for_update().get(pk=project_id)
    batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status not in ("complete", "incomplete"):
        raise ValueError("Only complete batches can be indexed")
    context = {}
    if batch.source == "gsc" and batch.method == "upload":
        context["gsc_problem"] = batch.records.filter(data__sheet="Metadata", data__values__Property="Probleem").values_list("data__values__Waarde", flat=True).first()
    URLObservation.objects.filter(batch=batch).delete()
    iterator = batch.records.order_by("id").iterator(chunk_size=50)
    count = 0
    summary={"version":"url-v1","page_records":0,"subtree_records":0,"rejected_url_records":0,"non_url_records":0,"rejected_record_ids":[]}
    while True:
        chunk = list(islice(iterator, 50))
        if not chunk:
            break
        adapted = []
        unique = {}
        for record in chunk:
            result = adapt_record(batch, record.data, context)
            if result is not None:
                adapted.append((record, result))
                unique[result["url_hash"]] = result
                summary[result["scope"]+"_records"] += 1
            else:
                data=record.data if isinstance(record.data,dict) else {}
                values=data.get("values") if isinstance(data.get("values"),dict) else data
                candidate=data.get("url") or first(values,*URL_COLUMNS)
                if candidate is not None:
                    summary["rejected_url_records"] += 1
                    if len(summary["rejected_record_ids"])<10:
                        summary["rejected_record_ids"].append(record.pk)
                else:
                    summary["non_url_records"] += 1
        ProjectURL.objects.bulk_create([ProjectURL(project_id=project_id,normalized_url=value["normalized_url"],url_hash=key,host=value["host"]) for key,value in unique.items()],ignore_conflicts=True,batch_size=500)
        urls = {url.url_hash:url for url in ProjectURL.objects.filter(project_id=project_id,url_hash__in=unique)}
        observations = []
        for record, result in adapted:
            url = urls[result["url_hash"]]
            if url.normalized_url != result["normalized_url"]:
                raise ValueError("URL identity collision")
            observations.append(URLObservation(url=url,batch=batch,source_record=record,original_url=result["original_url"],
                scope=result["scope"],measured_date=result["measured_date"],measured_at=result["measured_at"],facts=result["facts"]))
        URLObservation.objects.bulk_create(observations,batch_size=500)
        count += len(observations)
    prune_urls(project_id)
    summary["indexed_records"]=count
    batch.url_index_info=summary
    batch.save(update_fields=["url_index_info"])
    if derive_assessment:
        from assessments.services import rebuild_batch
        rebuild_batch(batch.pk, recover_documents=recover_documents)
    return count
