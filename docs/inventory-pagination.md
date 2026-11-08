# Database-backed URL inventory

The inventory, dataset filters, facets, counts and sorting run against a rebuildable PostgreSQL read model. HTTP requests fetch 100 unique URLs and the fields needed to display that page. Source records remain authoritative. Field identities, observation ranks, exact URL identity and user preferences are preserved.

## Lifecycle

`InventoryState` tracks source revision and active generation. Project host-rule changes, batch saves/deletions and source record changes invalidate the revision transactionally, including queryset bulk operations. Direct SQL maintenance must call `sources.inventory_index.invalidate(project_id)` in the same transaction.

Celery beat finds pending projects every 30 seconds and schedules independent rebuild tasks. An advisory transaction lock prevents simultaneous builders for one project. Records are sorted by primary URL in PostgreSQL and streamed through the existing observation-selection code. Python buffers one URL's observations, 25 projected URLs and 250 insert objects. Typed values are stored on insert; field type inference retains only summaries. Field cardinalities up to ten, small choice counts, populated-value counts and project metrics are precomputed. Filtered totals and visible-field counts share one aggregate query. Filter and sort joins use the unique URL/field key, avoiding per-URL scalar lookups. A revision check prevents publishing a generation built from changed inputs. Failure rolls back the generation, leaving the project pending for the next sweep. Replacement deletes old values and URLs directly in SQL so Django does not materialize the previous generation during cascade collection.

Pages use repeatable-read snapshots so generation replacement cannot leave an in-flight page partially populated. Preference writes occur after that snapshot commits. While a project is pending, the overview/performance page returns HTTP 202 with a reload action. Stale projections are never served after deletion or host-rule changes.

## Other consumers

Performance reads only the chosen X field, traffic and content-type fields in batches. It still returns every eligible chart point; its response size grows with the number of points.

Production exports return HTTP 202 and a polling URL. Celery builds the workbook with bounded URL batches and streams source observations. Downloads require current project access and the requesting user owns the export token. Queued exports are retried by a durable dispatcher; stale attempts can resume after a worker restart. Artifacts are kept in private media and removed after 24 hours by an hourly task. Do not publish `MEDIA_ROOT/private-exports` through a proxy. Exports share the existing Celery worker with imports and inventory builds.

## Release

Use the existing production deployment workflow. Migrations 0019–0021 create the read model, mark existing projects pending and add private export jobs. Start web, worker and beat from the same release. Initial project views display the update message until the first background build completes. No source records are rewritten by the migrations. Rollback requires restoring the previous application image; derived tables can remain until a later schema cleanup.

`INVENTORY_BUILD_INLINE` is enabled only in disposable test settings. It must remain disabled in production. Automated tests run via `ops/test.sh` against a temporary PostgreSQL database, never production.

## Scale verification

`ops/checks/inventory-scale.py` creates 100,000 synthetic unique URLs with SF and Ahrefs observations in an isolated database using `config.test_settings`. It rebuilds the projection and measures two concurrent filtered, sorted page requests. It asserts the disposable database identity before writing. Run it in a temporary internal Docker network with PostgreSQL aliased as `database`, the application limited to 512 MiB, and disposable database storage on disk (the normal test runner's 256 MiB tmpfs is too small).

Numbered pages currently use LIMIT/OFFSET. Deep pages still require the database to skip preceding matches. Saved choice filters retain their complete choice lists, so a formerly small field that later gains many distinct values can produce a large filter dropdown. Unknown field types are inferred from current winning observations. The projection adds database storage proportional to unique URL/field pairs, in exchange for bounded Python memory during table requests.

### Measured on 8 November 2026

Synthetic fixture: 100,000 unique URLs, 200,000 SF/Ahrefs observations, 13 source fields. Application and disposable PostgreSQL containers each had a 512 MiB limit. Requests used two concurrent Django test clients in one application process; these are local application measurements, not internet response times.

| Scenario | Time | Peak process RSS |
| --- | --- | --- |
| Rebuild and replace an existing generation | 203 s | 103 MiB |
| Two filtered/sorted table pages, including page 500 | 6.0 / 6.1 s | 130 MiB |
| Table page alongside the complete chart | 5.3 / 13.2 s | 220 MiB |

Both final scale runs completed without OOM kills under the 512 MiB limit. The normal overview without filters uses precomputed totals; filtered pages still do database work proportional to the matching dataset. Large charts retain all eligible points and can produce substantial HTML/JSON responses (about 9 MB in this fixture).
