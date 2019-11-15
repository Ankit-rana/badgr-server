# encoding: utf-8
from __future__ import unicode_literals

import json
from urllib import quote

from openbadges.verifier.openbadges_context import OPENBADGES_CONTEXT_V2_URI, OPENBADGES_CONTEXT_V2_DICT
import responses
import urlparse

from django.utils.encoding import force_text
from rest_framework.fields import DateTimeField

from backpack.tests.utils import setup_resources
from mainsite.models import BadgrApp
from mainsite.tests import BadgrTestCase, SetupIssuerHelper


class ManifestFileTests(BadgrTestCase):
    def test_can_retrieve_manifest_files(self):
        ba = BadgrApp.objects.create(name='test', cors='some.domain.com')
        response = self.client.get('/bc/v1/manifest/some.domain.com', headers={'Accept': 'application/json'})
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data['@context'], 'https://w3id.org/openbadges/badgeconnect/v1')
        self.assertIn('https://purl.imsglobal.org/spec/ob/v2p1/scope/assertion.readonly', data['badgeConnectAPI'][0]['scopesOffered'])

        response = self.client.get('/bc/v1/manifest/some.otherdomain.com', headers={'Accept': 'application/json'})
        self.assertEqual(response.status_code, 404)

        response = self.client.get('/.well-known/badgeconnect.json')
        self.assertEqual(response.status_code, 302)

        url = urlparse.urlparse(response._headers['location'][1])
        self.assertIn('/bc/v1/manifest/', url.path)

    def test_manifest_file_is_theme_appropriate(self):
        ba = BadgrApp.objects.create(name='test', cors='some.domain.com')
        response = self.client.get('/bc/v1/manifest/some.domain.com', headers={'Accept': 'application/json'})
        data = response.data
        self.assertEqual(data['badgeConnectAPI'][0]['name'], ba.name)


class BadgeConnectOAuthTests(BadgrTestCase, SetupIssuerHelper):
    @responses.activate
    def test_can_register_and_auth_badge_connect_app(self):
        requested_scopes = [
            "https://purl.imsglobal.org/spec/ob/v2p1/scope/assertion.readonly",
            "https://purl.imsglobal.org/spec/ob/v2p1/scope/assertion.create",
            "https://purl.imsglobal.org/spec/ob/v2p1/scope/profile.readonly",
        ]
        registration_data = {
            "client_name": "Badge Issuer",
            "client_uri": "https://issuer.example.com",
            "logo_uri": "https://issuer.example.com/logo.png",
            "tos_uri": "https://issuer.example.com/terms-of-service",
            "policy_uri": "https://issuer.example.com/privacy-policy",
            "software_id": "13dcdc83-fc0d-4c8d-9159-6461da297388",
            "software_version": "54dfc83-fc0d-4c8d-9159-6461da297388",
            "redirect_uris": [
                "https://issuer.example.com/o/redirect"
            ],
            "token_endpoint_auth_method": "client_secret_basic",
            "grant_types": [
                "authorization_code",
                "refresh_token"
            ],
            "response_types": [
                "code"
            ],
            "scope": ' ' .join(requested_scopes)
        }

        user = self.setup_user(email='test@example.com', authenticate=True)

        response = self.client.post('/o/register', registration_data)
        client_id = response.data['client_id']
        url = '/o/authorize'
        data = {
            "allow": True,
            "response_type": "code",
            "client_id": response.data['client_id'],
            "redirect_uri": registration_data['redirect_uris'][0],
            "scopes": requested_scopes,
            "state": ""
        }
        response = self.client.post(url, data=data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success_url'].startswith(registration_data['redirect_uris'][0]))
        url = urlparse.urlparse(response.data['success_url'])
        code = urlparse.parse_qs(url.query)['code'][0]

        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': client_id,
            'redirect_uri': registration_data['redirect_uris'][0],
            'scope': ' '.join(requested_scopes),
        }
        response = self.client.post('/o/token', data=data)
        self.assertEqual(response.status_code, 200)

        self.client.logout()

        token_data = json.loads(response.content)
        self.assertTrue('refresh_token' in token_data)
        access_token = token_data['access_token']

        test_issuer_user = self.setup_user(authenticate=False)
        test_issuer = self.setup_issuer(owner=test_issuer_user)
        test_badgeclass = self.setup_badgeclass(issuer=test_issuer)
        assertion = test_badgeclass.issue(user.email, notify=False)

        # Get the assertion
        self.client.credentials(HTTP_AUTHORIZATION='Bearer {}'.format(access_token))
        response = self.client.get('/bc/v1/assertions')
        self.assertEqual(response.status_code, 200)

        REMOTE_BADGE_URI = 'http://a.com/assertion-embedded1'
        setup_resources([
            {'url': REMOTE_BADGE_URI, 'filename': '2_0_assertion_embedded_badgeclass.json'},
            {'url': OPENBADGES_CONTEXT_V2_URI, 'response_body': json.dumps(OPENBADGES_CONTEXT_V2_DICT)},
            {'url': 'http://a.com/badgeclass_image', 'filename': "unbaked_image.png"},
        ])
        # Post new external assertion
        assertion.save()

        expected_status = {
            "error": None,
            "statusCode": 200,
            "statusText": 'OK'
        }

        response = self.client.post('/bc/v1/assertions', data={'id': REMOTE_BADGE_URI}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertJSONEqual(force_text(response.content), {
            "status": expected_status
        })

        response = self.client.get('/bc/v1/assertions')
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(force_text(json.dumps(response.data['status'])), expected_status)
        self.assertEqual(len(response.data['results']), 2)
        ids = [response.data['results'][0]['id'], response.data['results'][1]['id']]
        self.assertTrue(assertion.jsonld_id in ids)
        self.assertTrue(REMOTE_BADGE_URI in ids)
        for result in response.data['results']:
            self.assertEqual(result['@context'], OPENBADGES_CONTEXT_V2_URI)
            self.assertEqual(result['type'], 'Assertion')

        response = self.client.get('/bc/v1/profile')
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(force_text(response.content), {
            "status": expected_status,
            "results": [
                {
                    "@context": "https://w3id.org/openbadges/v2",
                    "name": "firsty lastington",
                    "email": "test@example.com"
                }
            ]
        })

    def test_reject_duplicate_redirect_uris(self):
        registration_data = {
            "client_name": "Badge Issuer",
            "client_uri": "https://issuer.example.com",
            "logo_uri": "https://issuer.example.com/logo.png",
            "tos_uri": "https://issuer.example.com/terms-of-service",
            "policy_uri": "https://issuer.example.com/privacy-policy",
            "software_id": "13dcdc83-fc0d-4c8d-9159-6461da297388",
            "software_version": "54dfc83-fc0d-4c8d-9159-6461da297388",
            "redirect_uris": [
                "https://issuer.example.com/o/redirect"
            ],
            "token_endpoint_auth_method": "client_secret_basic",
            "grant_types": [
                "authorization_code",
                "refresh_token"
            ],
            "response_types": [
                "code"
            ],
        }
        user = self.setup_user(email='test@example.com', authenticate=True)

        response = self.client.post('/o/register', registration_data)
        self.assertTrue('client_id' in response.data)

        registration_data['client_uri'] += '?foo'
        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "Redirect URI already registered")


    def test_reject_different_domains(self):
        registration_data = {
            "client_name": "Badge Issuer",
            "client_uri": "https://issuer.example.com",
            "logo_uri": "https://issuer.example.com/logo.png",
            "tos_uri": "https://issuer.example.com/terms-of-service",
            "policy_uri": "https://issuer.example.com/privacy-policy",
            "software_id": "13dcdc83-fc0d-4c8d-9159-6461da297388",
            "software_version": "54dfc83-fc0d-4c8d-9159-6461da297388",
            "redirect_uris": [
                "https://issuer2.example.com/o/redirect"
            ],
            "token_endpoint_auth_method": "client_secret_basic",
            "grant_types": [
                "authorization_code",
                "refresh_token"
            ],
            "response_types": [
                "code"
            ],
        }
        user = self.setup_user(email='test@example.com', authenticate=True)

        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URIs do not match")
        registration_data['redirect_uris'][0] = "https://issuer2.example.com/o/redirect"
        registration_data['logo_uri'] = "https://issuer2.example.com/logo.png"
        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URIs do not match")
        registration_data['logo_uri'] = "https://issuer.example.com/logo.png"
        registration_data['tos_uri'] = "https://issuer2.example.com/terms-of-service"
        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URIs do not match")
        registration_data['tos_uri'] = "https://issuer.example.com/terms-of-service"
        registration_data['policy_uri'] = "https://issuer2.example.com/privacy-policy"
        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URIs do not match")
        registration_data['policy_uri'] = "https://issuer.example.com/privacy-policy"
        registration_data['client_uri'] = "https://issuer2.example.com"
        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URIs do not match")

    def test_all_https_uris(self):
        registration_data = {
            "client_name": "Badge Issuer",
            "client_uri": "https://issuer.example.com",
            "logo_uri": "https://issuer.example.com/logo.png",
            "tos_uri": "https://issuer.example.com/terms-of-service",
            "policy_uri": "https://issuer.example.com/privacy-policy",
            "software_id": "13dcdc83-fc0d-4c8d-9159-6461da297388",
            "software_version": "54dfc83-fc0d-4c8d-9159-6461da297388",
            "redirect_uris": [
                "http://issuer.example.com/o/redirect"
            ],
            "token_endpoint_auth_method": "client_secret_basic",
            "grant_types": [
                "authorization_code",
                "refresh_token"
            ],
            "response_types": [
                "code"
            ],
        }
        user = self.setup_user(email='test@example.com', authenticate=True)

        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URI schemes must be HTTPS")
        registration_data['redirect_uris'][0] = "https://issuer.example.com/o/redirect"
        registration_data['logo_uri'] = "http://issuer.example.com/logo.png"
        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URI schemes must be HTTPS")
        registration_data['logo_uri'] = "https://issuer.example.com/logo.png"
        registration_data['tos_uri'] = "http://issuer.example.com/terms-of-service"
        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URI schemes must be HTTPS")
        registration_data['tos_uri'] = "https://issuer.example.com/terms-of-service"
        registration_data['policy_uri'] = "http://issuer.example.com/privacy-policy"
        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URI schemes must be HTTPS")
        registration_data['policy_uri'] = "https://issuer.example.com/privacy-policy"
        registration_data['client_uri'] = "http://issuer.example.com"
        response = self.client.post('/o/register', registration_data)
        self.assertEqual(response.data['error'], "URI schemes must be HTTPS")

    def test_no_refresh_token(self):
        requested_scopes = [
            "https://purl.imsglobal.org/spec/ob/v2p1/scope/assertion.readonly",
            "https://purl.imsglobal.org/spec/ob/v2p1/scope/assertion.create",
            "https://purl.imsglobal.org/spec/ob/v2p1/scope/profile.readonly",
        ]
        registration_data = {
            "client_name": "Badge Issuer",
            "client_uri": "https://issuer.example.com",
            "logo_uri": "https://issuer.example.com/logo.png",
            "tos_uri": "https://issuer.example.com/terms-of-service",
            "policy_uri": "https://issuer.example.com/privacy-policy",
            "software_id": "13dcdc83-fc0d-4c8d-9159-6461da297388",
            "software_version": "54dfc83-fc0d-4c8d-9159-6461da297388",
            "redirect_uris": [
                "https://issuer.example.com/o/redirect"
            ],
            "token_endpoint_auth_method": "client_secret_basic",
            "grant_types": [
                "authorization_code",
            ],
            "response_types": [
                "code"
            ],
            "scope": ' '.join(requested_scopes)
        }

        user = self.setup_user(email='test@example.com', authenticate=True)

        response = self.client.post('/o/register', registration_data)
        client_id = response.data['client_id']
        url = '/o/authorize'
        data = {
            "allow": True,
            "response_type": "code",
            "client_id": response.data['client_id'],
            "redirect_uri": registration_data['redirect_uris'][0],
            "scopes": requested_scopes,
            "state": ""
        }
        response = self.client.post(url, data=data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success_url'].startswith(registration_data['redirect_uris'][0]))
        url = urlparse.urlparse(response.data['success_url'])
        code = urlparse.parse_qs(url.query)['code'][0]

        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': client_id,
            'redirect_uri': registration_data['redirect_uris'][0],
            'scope': ' '.join(requested_scopes),
        }
        response = self.client.post('/o/token', data=data)
        self.assertEqual(response.status_code, 200)

        token_data = json.loads(response.content)
        self.assertTrue('refresh_token' not in token_data)



class BadgeConnectAPITests(BadgrTestCase, SetupIssuerHelper):

    def test_unauthenticated_requests(self):
        expected_response = {
            "status": {
                "error": None,
                "statusCode": 401,
                "statusText": 'UNAUTHENTICATED'
            }
        }
        
        response = self.client.get('/bc/v1/assertions')
        self.assertEquals(response.status_code, 401)
        self.assertJSONEqual(force_text(response.content), expected_response)

        response = self.client.post('/bc/v1/assertions', data={'id': 'http://a.com/assertion-embedded1'}, format='json')
        self.assertEquals(response.status_code, 401)
        self.assertJSONEqual(force_text(response.content), expected_response)

        response = self.client.get('/bc/v1/profile')
        self.assertEqual(response.status_code, 401)
        self.assertJSONEqual(force_text(response.content), expected_response)


    def test_assertions_pagination(self):
        self.user = self.setup_user(authenticate=True)

        test_issuer_user = self.setup_user(authenticate=False)
        test_issuer = self.setup_issuer(owner=test_issuer_user)
        assertions = []
        for _ in range(25):
            test_badgeclass = self.setup_badgeclass(issuer=test_issuer)
            assertions.append(test_badgeclass.issue(self.user.email, notify=False))
        response = self.client.get('/bc/v1/assertions?limit=10&offset=0')
        self.assertEqual(len(response.data['results']), 10)
        self.assertTrue(response.has_header('Link'))
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=10>; rel="next"' in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=20>; rel="last"' in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=0>; rel="first"' in response['Link'])
        for x in range(0, 10):
            self.assertEqual(response.data['results'][x]['id'], assertions[24 - x].jsonld_id)

        response = self.client.get('/bc/v1/assertions?limit=10&offset=10')
        self.assertEqual(len(response.data['results']), 10)
        self.assertTrue(response.has_header('Link'))
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=20>; rel="next"' in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=20>; rel="last"' in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=0>; rel="first"' in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=0>; rel="prev"' in response['Link'])
        for x in range(0, 10):
            self.assertEqual(response.data['results'][x]['id'], assertions[24 - (x + 10)].jsonld_id)

        response = self.client.get('/bc/v1/assertions?limit=10&offset=20')
        self.assertEqual(len(response.data['results']), 5)
        self.assertTrue(response.has_header('Link'))
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=20>; rel="last"' in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=0>; rel="first"' in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=10>; rel="prev"' in response['Link'])
        for x in range(0, 5):
            self.assertEqual(response.data['results'][x]['id'], assertions[24 - (x + 20)].jsonld_id)

        since = quote(DateTimeField().to_representation(assertions[5].created_at))
        response = self.client.get('/bc/v1/assertions?limit=10&offset=0&since=' + since)
        self.assertEqual(len(response.data['results']), 10)
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=10&since=%s>; rel="next"' % since in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=10&since=%s>; rel="last"' % since in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=0&since=%s>; rel="first"' % since in response['Link'])
        for x in range(0, 10):
            self.assertEqual(response.data['results'][x]['id'], assertions[24 - x].jsonld_id)

        response = self.client.get('/bc/v1/assertions?limit=10&offset=10&since=' + since)
        self.assertEqual(len(response.data['results']), 10)
        self.assertTrue(response.has_header('Link'))
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=10&since=%s>; rel="last"' % since in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=0&since=%s>; rel="first"' % since in response['Link'])
        self.assertTrue('<http://testserver/bc/v1/assertions?limit=10&offset=0&since=%s>; rel="prev"' % since in response['Link'])
        for x in range(0, 10):
            self.assertEqual(response.data['results'][x]['id'], assertions[24 - (x + 10)].jsonld_id)
