from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from projects.models import Project, Membership
from .adapters import normalize_url
from .labels import label_key
from .models import Label, URLLabel, ProjectURL
from .path_labels import assign_path_labels


class PathLabelTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='path-editor')
        self.project = Project.objects.create(name='GMW',website='https://www.example.com/',created_by=self.actor)
        Membership.objects.create(project=self.project,user=self.actor,role='editor')
        self.work = Label.objects.create(project=self.project,name='Arbeid',key=label_key('Arbeid'),url_path='/expertises/arbeid/',url_path_en='/en/expertises/employment/')
        self.estate = Label.objects.create(project=self.project,name='Vastgoed',key=label_key('Vastgoed'),url_path_en='/en/expertises/real-estate/')
        self.build = Label.objects.create(project=self.project,name='Bouw',key=label_key('Bouw'),url_path_en='/en/expertises/real-estate/construction/')
        self.cassation = Label.objects.create(project=self.project,name='Cassatie',key=label_key('Cassatie'))
        for address in ['https://www.example.com/en/expertises/employment','https://www.example.com/en/expertises/employment/','https://www.example.com/en/expertises/employment/contract/','https://www.example.com/en/expertises/employment-extra/','https://www.example.com/en/expertises/real-estate/construction/','https://www.example.com/expertises/arbeid/','https://other.test/en/expertises/employment/']:
            normalized, host, digest = normalize_url(address)
            ProjectURL.objects.create(project=self.project,normalized_url=normalized,host=host,url_hash=digest)

    def test_dry_run_segment_boundaries_and_multiple_labels(self):
        result=assign_path_labels(project_id=self.project.pk,actor=self.actor)
        self.assertEqual(result['matched_assignments'],5)
        self.assertEqual(result['unique_urls'],4)
        self.assertEqual(result['created_assignments'],0)
        self.assertFalse(URLLabel.objects.exists())
        self.assertEqual(result['skipped'][0]['label'],'Cassatie')

    def test_apply_preserves_existing_and_repeats_without_duplicates(self):
        nl=ProjectURL.objects.get(normalized_url='https://www.example.com/expertises/arbeid/')
        previous=URLLabel.objects.create(url=nl,label=self.work,status='reviewed',role='primary')
        first=assign_path_labels(project_id=self.project.pk,actor=self.actor,apply=True)
        self.assertEqual(first['created_assignments'],5)
        self.assertEqual(URLLabel.objects.count(),6)
        previous.refresh_from_db()
        self.assertEqual((previous.status,previous.role),('reviewed','primary'))
        again=assign_path_labels(project_id=self.project.pk,actor=self.actor,apply=True)
        self.assertEqual(again['created_assignments'],0)
        self.assertEqual(URLLabel.objects.count(),6)

    def test_access_and_invalid_rule(self):
        outsider=get_user_model().objects.create_user(username='outsider-path')
        with self.assertRaises(PermissionDenied):
            assign_path_labels(project_id=self.project.pk,actor=outsider,apply=True)
        self.work.url_path_en='//other.test/en/'
        self.work.save()
        with self.assertRaises(ValueError):
            assign_path_labels(project_id=self.project.pk,actor=self.actor,apply=True)
        self.assertFalse(URLLabel.objects.exists())
