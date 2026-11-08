# Skylight write API

Captured on 2026-11-08 from the Skylight web app (ourskylight.com) by creating, editing, completing and deleting a one-off test chore for Dad. All requests use the same bearer token and `skylight-api-version` header as the read endpoints. The API is unofficial and can change.

| Action | Request | Response |
|---|---|---|
| Create | `POST /api/frames/{frame}/chores/create_multiple` | 200, `{"data":[chore, …]}`, one chore per category |
| Edit | `PUT /api/frames/{frame}/chores/{id}` | 200, `{"data":chore}` |
| Complete | `PUT /api/frames/{frame}/chores/{id}/completions` | 200, `{"data":chore}` with `status: complete` |
| Delete | `DELETE /api/frames/{frame}/chores/{id}` | (navigated away before the response was read) |

Bodies are flat JSON, not JSON:API.

**Create**
```json
{"start":"2026-11-08","up_for_grabs":false,"routine":false,"start_time":"18:30",
 "recurrence_set":null,"renewal_interval":null,"renewal_unit":null,"recurring_until":null,
 "summary":"todosky test","category_ids":["<dad category id>"]}
```
Without a due time, `start_time` is `null`. The returned chore has a plain numeric id (no `-YYYY-MM-DD` suffix, since it isn't recurring) and a `category` relationship.

**Edit** — the app sends only the fields that changed:
```json
{"summary":"todosky test edited"}
{"start":"2026-11-09","start_time":null}
```

**Complete**
```json
{"status":"complete","completed_on":"2026-11-08"}
```
After completion the chore also gets a `completed_category` relationship and a `completed_at` timestamp.

**Delete** — the route was read from the web app's JavaScript (`delete(/frames/${frame}/chores/${id}, {apply_to, apply_to_profiles})`). `apply_to` matters only for recurring series and is omitted for one-off chores. The test chore disappeared from the frame afterwards.

Side note: the web app's Add Task form also saves the title to the Task Box (`POST /api/frames/{frame}/task_box/items`) when "Save to Task Box" is ticked. todosky doesn't do this.
