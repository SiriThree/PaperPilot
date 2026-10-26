CREATE TABLE users (
 user_id TEXT PRIMARY KEY NOT NULL, username TEXT UNIQUE NOT NULL,
 display_name TEXT NOT NULL
);
CREATE TABLE projects (
 project_id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
 description TEXT NOT NULL, owner_id TEXT NOT NULL REFERENCES users(user_id),
 FOREIGN KEY(project_id,owner_id) REFERENCES project_members(project_id,user_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE project_members (
 project_id TEXT NOT NULL REFERENCES projects(project_id), user_id TEXT NOT NULL REFERENCES users(user_id),
 role TEXT NOT NULL CHECK(role IN ('owner','member')), PRIMARY KEY(project_id,user_id)
);
CREATE UNIQUE INDEX one_owner_per_project ON project_members(project_id) WHERE role='owner';
CREATE TRIGGER member_role_insert BEFORE INSERT ON project_members
WHEN (NEW.role='owner') != (NEW.user_id=(SELECT owner_id FROM projects WHERE project_id=NEW.project_id))
BEGIN SELECT RAISE(ABORT,'invalid owner relationship'); END;
CREATE TRIGGER member_role_update BEFORE UPDATE ON project_members
WHEN (NEW.role='owner') != (NEW.user_id=(SELECT owner_id FROM projects WHERE project_id=NEW.project_id))
BEGIN SELECT RAISE(ABORT,'invalid owner relationship'); END;
CREATE TRIGGER immutable_owner BEFORE UPDATE OF owner_id ON projects
BEGIN SELECT RAISE(ABORT,'owner transfer unsupported'); END;
CREATE TABLE papers (
 paper_id TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
 year INTEGER CHECK(year IS NULL OR year BETWEEN 1000 AND 3000)
);
CREATE TABLE project_papers (
 project_id TEXT NOT NULL REFERENCES projects(project_id), paper_id TEXT NOT NULL UNIQUE REFERENCES papers(paper_id),
 digest TEXT NOT NULL CHECK(length(digest)=64 AND digest NOT GLOB '*[^0-9a-f]*'),
 storage_key TEXT NOT NULL CHECK(length(storage_key)>0),
 status TEXT NOT NULL CHECK(status IN ('pending','processing','searchable','failed')),
 removed INTEGER NOT NULL DEFAULT 0 CHECK(removed IN (0,1)),
 PRIMARY KEY(project_id,paper_id), UNIQUE(project_id,digest)
);
CREATE INDEX project_paper_visibility ON project_papers(project_id,removed,status);
CREATE TABLE tasks (
 task_id TEXT PRIMARY KEY NOT NULL, project_id TEXT NOT NULL REFERENCES projects(project_id),
 actor_id TEXT NOT NULL REFERENCES users(user_id), request_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('process','answer','evidence','draft')),
 status TEXT NOT NULL CHECK(status IN ('pending','processing','succeeded','failed')),
 attempts INTEGER NOT NULL CHECK(attempts>=0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)),
 result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
 result_ref_json TEXT CHECK(result_ref_json IS NULL OR json_valid(result_ref_json)),
 lease_until TEXT,
 UNIQUE(task_id,project_id),
 CHECK((status='succeeded' AND result_json IS NOT NULL AND result_ref_json IS NOT NULL AND error_json IS NULL)
 OR (status='failed' AND error_json IS NOT NULL AND result_json IS NULL AND result_ref_json IS NULL)
 OR (status IN ('pending','processing') AND error_json IS NULL AND result_json IS NULL AND result_ref_json IS NULL)),
 CHECK((status='processing' AND lease_until IS NOT NULL AND attempts>0) OR (status!='processing' AND lease_until IS NULL))
);
CREATE INDEX task_claim ON tasks(status,lease_until,created_at);
CREATE TABLE task_papers (
 task_id TEXT NOT NULL, project_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 PRIMARY KEY(task_id,paper_id),
 FOREIGN KEY(task_id,project_id) REFERENCES tasks(task_id,project_id),
 FOREIGN KEY(project_id,paper_id) REFERENCES project_papers(project_id,paper_id)
);
CREATE TABLE sources (
 chunk_id TEXT PRIMARY KEY NOT NULL, project_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 citation_id TEXT NOT NULL, page_number INTEGER NOT NULL CHECK(page_number>=1),
 excerpt TEXT NOT NULL CHECK(length(excerpt) BETWEEN 1 AND 4000),
 synthetic INTEGER NOT NULL CHECK(synthetic IN (0,1)),
 UNIQUE(chunk_id,project_id,paper_id),
 FOREIGN KEY(project_id,paper_id) REFERENCES project_papers(project_id,paper_id)
);
CREATE INDEX paper_sources ON sources(project_id,paper_id);
CREATE TABLE evidence (
 evidence_id TEXT PRIMARY KEY NOT NULL, task_id TEXT NOT NULL UNIQUE,
 project_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 result_json TEXT NOT NULL CHECK(json_valid(result_json)),
 UNIQUE(evidence_id,project_id,paper_id),
 FOREIGN KEY(task_id,project_id) REFERENCES tasks(task_id,project_id),
 FOREIGN KEY(project_id,paper_id) REFERENCES project_papers(project_id,paper_id)
);
CREATE TABLE evidence_sources (
 evidence_id TEXT NOT NULL, chunk_id TEXT NOT NULL, project_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 PRIMARY KEY(evidence_id,chunk_id),
 FOREIGN KEY(evidence_id,project_id,paper_id) REFERENCES evidence(evidence_id,project_id,paper_id),
 FOREIGN KEY(chunk_id,project_id,paper_id) REFERENCES sources(chunk_id,project_id,paper_id)
);
CREATE TABLE reports (
 report_id TEXT PRIMARY KEY NOT NULL, project_id TEXT NOT NULL REFERENCES projects(project_id),
 UNIQUE(report_id,project_id)
);
CREATE TABLE report_versions (
 version_id TEXT PRIMARY KEY NOT NULL, report_id TEXT NOT NULL, project_id TEXT NOT NULL,
 number INTEGER NOT NULL CHECK(number>=1), title TEXT NOT NULL, research_question TEXT NOT NULL,
 body TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(report_id,number), UNIQUE(version_id,project_id),
 FOREIGN KEY(report_id,project_id) REFERENCES reports(report_id,project_id)
);
CREATE TABLE report_sources (
 version_id TEXT NOT NULL, project_id TEXT NOT NULL, citation_id TEXT NOT NULL,
 paper_id TEXT NOT NULL, chunk_id TEXT NOT NULL, page_number INTEGER NOT NULL CHECK(page_number>=1),
 excerpt TEXT NOT NULL, synthetic INTEGER NOT NULL CHECK(synthetic IN (0,1)),
 PRIMARY KEY(version_id,citation_id),
 FOREIGN KEY(version_id,project_id) REFERENCES report_versions(version_id,project_id),
 FOREIGN KEY(chunk_id,project_id,paper_id) REFERENCES sources(chunk_id,project_id,paper_id)
);
CREATE TRIGGER immutable_report_version_update BEFORE UPDATE ON report_versions
BEGIN SELECT RAISE(ABORT,'immutable report version'); END;
CREATE TRIGGER immutable_report_version_delete BEFORE DELETE ON report_versions
BEGIN SELECT RAISE(ABORT,'immutable report version'); END;
CREATE TRIGGER immutable_report_source_update BEFORE UPDATE ON report_sources
BEGIN SELECT RAISE(ABORT,'immutable report source'); END;
CREATE TRIGGER immutable_report_source_delete BEFORE DELETE ON report_sources
BEGIN SELECT RAISE(ABORT,'immutable report source'); END;
CREATE TABLE idempotency (
 user_id TEXT NOT NULL REFERENCES users(user_id), project_id TEXT NOT NULL REFERENCES projects(project_id),
 operation TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 response_json TEXT NOT NULL CHECK(json_valid(response_json)), PRIMARY KEY(user_id,project_id,operation,key)
);
CREATE TABLE audit_events (
 event_id TEXT PRIMARY KEY NOT NULL, timestamp TEXT NOT NULL, request_id TEXT NOT NULL,
 actor_id TEXT REFERENCES users(user_id), project_id TEXT REFERENCES projects(project_id),
 action TEXT NOT NULL, decision TEXT NOT NULL, event_json TEXT NOT NULL CHECK(json_valid(event_json))
);
CREATE INDEX project_audit ON audit_events(project_id,timestamp DESC,event_id DESC);
CREATE TRIGGER immutable_audit_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT,'append only audit'); END;
CREATE TRIGGER immutable_audit_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT,'append only audit'); END;
CREATE TABLE domain_events (
 event_id TEXT PRIMARY KEY NOT NULL, timestamp TEXT NOT NULL, request_id TEXT,
 actor_id TEXT REFERENCES users(user_id), project_id TEXT REFERENCES projects(project_id),
 operation TEXT NOT NULL, resource_id TEXT
);
CREATE TRIGGER immutable_domain_update BEFORE UPDATE ON domain_events
BEGIN SELECT RAISE(ABORT,'append only domain event'); END;
CREATE TRIGGER immutable_domain_delete BEFORE DELETE ON domain_events
BEGIN SELECT RAISE(ABORT,'append only domain event'); END;
CREATE TABLE topics (topic_id TEXT PRIMARY KEY NOT NULL, name TEXT UNIQUE NOT NULL);
CREATE TABLE paper_topics (
 project_id TEXT NOT NULL, paper_id TEXT NOT NULL, topic_id TEXT NOT NULL REFERENCES topics(topic_id),
 PRIMARY KEY(project_id,paper_id,topic_id),
 FOREIGN KEY(project_id,paper_id) REFERENCES project_papers(project_id,paper_id)
);
