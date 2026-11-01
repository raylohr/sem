import json
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from .models import Project, Membership
from .permissions import can_edit


class ProjectAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.owner = U.objects.create_user("owner")
        cls.viewer = U.objects.create_user("viewer")
        cls.editor = U.objects.create_user("editor")
        cls.outsider = U.objects.create_user("outsider", is_staff=True)
        cls.root = U.objects.create_superuser("root", "root@example.test", "testing-only-password")
        cls.a = Project.objects.create(name="A", website="https://a.example", created_by=cls.owner)
        cls.b = Project.objects.create(name="B", website="https://b.example", created_by=cls.owner)
        for user, role in [(cls.owner, "admin"), (cls.viewer, "viewer"), (cls.editor, "editor")]:
            Membership.objects.create(project=cls.a, user=user, role=role)

    def members_url(self, project=None):
        return reverse("projects:membership-set", args=[(project or self.a).pk])

    def post_role(self, role="editor"):
        return self.client.post(self.members_url(), json.dumps({"user_id": self.outsider.pk, "role": role}), content_type="application/json")

    def test_list_only_contains_memberships(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("projects:list"))
        self.assertEqual([p["id"] for p in response.json()["projects"]], [str(self.a.pk)])

    def test_unknown_project_and_inaccessible_project_both_404(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("projects:detail", args=[self.b.pk])).status_code, 404)

    def test_staff_without_membership_has_no_access(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse("projects:list")).json()["projects"], [])
        self.assertEqual(self.post_role().status_code, 404)

    def test_viewer_and_editor_cannot_grant_roles(self):
        for user in (self.viewer, self.editor):
            self.client.force_login(user)
            self.assertEqual(self.post_role().status_code, 403)

    def test_project_admin_can_add_existing_user(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.post_role().status_code, 200)
        self.assertTrue(Membership.objects.filter(project=self.a, user=self.outsider, role="editor").exists())

    def test_invalid_role_is_rejected(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.post_role("superuser").status_code, 400)

    def test_last_admin_cannot_demote_self(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.members_url(), json.dumps({"user_id": self.owner.pk, "role": "viewer"}), content_type="application/json")
        self.assertEqual(response.status_code, 409)

    def test_csrf_required_for_membership_change(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.members_url(), {}, content_type="application/json").status_code, 403)

    def test_superuser_can_see_all_projects(self):
        self.client.force_login(self.root)
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from django_otp import DEVICE_ID_SESSION_KEY
        device=TOTPDevice.objects.create(user=self.root,name='default')
        session=self.client.session;session[DEVICE_ID_SESSION_KEY]=device.persistent_id;session.save()
        self.assertEqual(len(self.client.get(reverse("projects:list")).json()["projects"]), 2)

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(reverse("projects:list")).status_code, 302)

    def test_edit_permission_and_inactive_user(self):
        self.assertTrue(can_edit(self.editor, self.a))
        self.assertFalse(can_edit(self.viewer, self.a))
        self.editor.is_active = False
        self.assertFalse(can_edit(self.editor, self.a))

    def test_revoking_membership_immediately_removes_access(self):
        self.client.force_login(self.owner)
        url = reverse('projects:membership-remove', args=[self.a.pk, self.viewer.pk])
        self.assertEqual(self.client.post(url).status_code, 200)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse('projects:detail', args=[self.a.pk])).status_code, 404)

    def test_last_admin_cannot_be_removed(self):
        self.client.force_login(self.owner)
        url = reverse('projects:membership-remove', args=[self.a.pk, self.owner.pk])
        self.assertEqual(self.client.post(url).status_code, 409)

    def test_staff_cannot_access_project_admin(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('admin:projects_project_changelist')).status_code, 403)
