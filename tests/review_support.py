"""In-memory services for repeatable user journeys; never contact real accounts."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import case_context
from supabase_service import StaleCaseError


class MemorySupabase:
    client = None

    def __init__(self):
        self.conversations = {}
        self.messages = {}
        self.deletion = None
        self.contexts = {}
        self.drafts = {}

    def is_ready(self):
        return True

    def sign_in(self, email, password):
        return SimpleNamespace(
            user=SimpleNamespace(id="review-user", email=email, user_metadata={}),
            session=SimpleNamespace(access_token="review-token", refresh_token="review-refresh"),
        )

    def get_user_metadata(self, token):
        return {}

    def build_user_scoped_client(self, token):
        return self

    def create_conversation(self, client, user_id, title):
        cid = str(uuid4())
        self.conversations[cid] = dict(id=cid, title=title, archived_at=None, user_id=user_id)
        self.messages[cid] = []
        return cid

    def delete_empty_conversations(self, client, user_id):
        for cid in list(self.conversations):
            if not self.messages[cid]:
                self.delete_conversation(client, cid, user_id)

    def ensure_conversation_for_user(self, client, cid, uid):
        if cid not in self.conversations or self.conversations[cid]["user_id"] != uid:
            raise ValueError("Not found")

    def list_conversations(self, client, archived=False):
        return deepcopy([c for c in self.conversations.values() if bool(c["archived_at"]) == archived])

    def fetch_messages_for_conversation(self, client, cid):
        return deepcopy(self.messages[cid])

    def insert_message(self, client, message):
        self.messages[message["conversation_id"]].append(deepcopy(message))

    def get_conversation_title(self, client, cid, uid):
        return self.conversations[cid]["title"]

    def rename_conversation(self, client, cid, uid, title):
        self.ensure_conversation_for_user(client, cid, uid)
        self.conversations[cid]["title"] = title

    def set_conversation_archived(self, client, cid, uid, archived):
        self.ensure_conversation_for_user(client, cid, uid)
        self.conversations[cid]["archived_at"] = "2026-10-06" if archived else None

    def delete_conversation(self, client, cid, uid):
        self.ensure_conversation_for_user(client, cid, uid)
        del self.conversations[cid]
        del self.messages[cid]
        self.contexts.pop(cid, None)
        self.drafts = {key: d for key, d in self.drafts.items() if d["conversation_id"] != cid}

    def fetch_all_conversations_with_messages(self, client):
        return [dict(c, messages=deepcopy(self.messages[cid]), situation=self.get_case_context(client, cid)["context"],
                     form_drafts=self.list_form_drafts(client, cid, include_payload=True)) for cid, c in self.conversations.items()]

    def get_case_context(self, client, cid):
        return deepcopy(self.contexts.get(cid, {"context": case_context.defaults(), "revision": 0}))

    def save_case_context(self, client, cid, uid, context, revision):
        self.ensure_conversation_for_user(client, cid, uid)
        if self.get_case_context(client, cid)["revision"] != revision:
            raise StaleCaseError()
        self.contexts[cid] = {"context": case_context.validate(context), "revision": revision + 1}
        return deepcopy(self.contexts[cid])

    def create_form_draft(self, client, cid, uid, intake):
        self.ensure_conversation_for_user(client, cid, uid)
        draft_id = str(uuid4())
        self.drafts[draft_id] = dict(id=draft_id, conversation_id=cid, intake=deepcopy(intake), created_at="2026-10-06")
        return draft_id

    def get_form_draft(self, client, cid, draft_id):
        draft = self.drafts.get(draft_id)
        if not draft or draft["conversation_id"] != cid:
            raise ValueError("Not found")
        return deepcopy(draft)

    def list_form_drafts(self, client, cid, include_payload=False):
        return [deepcopy(d) if include_payload else {"id": d["id"], "created_at": d["created_at"]}
                for d in self.drafts.values() if d["conversation_id"] == cid]

    def get_pending_account_deletion(self, client, uid):
        return self.deletion

    def request_account_deletion(self, client, uid, days):
        self.deletion = {"purge_after": "2026-11-05T12:00:00+00:00"}

    def cancel_account_deletion(self, client, uid):
        existed = self.deletion is not None
        self.deletion = None
        return existed


class ReviewAI:
    """Predictable success, transient failure, and complete-form responses."""

    def __init__(self):
        self.failed = set()
        self.histories = []

    def is_ready(self):
        return True

    def generate_title(self, message, reply):
        return "Heating repair question"

    def generate_reply(self, history):
        self.histories.append(deepcopy(history))
        content = history[-1]["content"]
        if "fail once" in content.lower() and content not in self.failed:
            self.failed.add(content)
            raise RuntimeError("Simulated upstream outage")
        if "test pdf" in content.lower():
            import json
            from tests.test_form_service import FULL
            return json.dumps(FULL)
        return "**Test reply:** I understand your repair question. What have you already reported to your landlord?"
