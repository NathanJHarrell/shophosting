"""
Tests for webapp/leads/routes.py - Speed Battle routes
Following TDD: tests written first, implementation follows
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime


class TestSpeedBattleRoutes:
    """Test Speed Battle public routes"""

    # =========================================================================
    # GET /speed-battle - Landing Page Tests
    # =========================================================================

    def test_speed_battle_landing_page_returns_200(self, client):
        """Test landing page returns 200"""
        response = client.get('/speed-battle')
        assert response.status_code == 200

    @patch('leads.routes.SpeedBattle')
    def test_speed_battle_landing_with_ref_param(self, mock_battle_class, client):
        """Test landing page with ref param looks up referrer battle"""
        mock_battle = Mock()
        mock_battle.battle_uid = 'ref12345'
        mock_battle.challenger_url = 'https://example.com'
        mock_battle_class.get_by_uid.return_value = mock_battle

        response = client.get('/speed-battle?ref=ref12345')

        assert response.status_code == 200
        mock_battle_class.get_by_uid.assert_called_once_with('ref12345')

    @patch('leads.routes.SpeedBattle')
    def test_speed_battle_landing_with_invalid_ref(self, mock_battle_class, client):
        """Test landing page with invalid ref param still returns 200"""
        mock_battle_class.get_by_uid.return_value = None

        response = client.get('/speed-battle?ref=invalidref')

        assert response.status_code == 200

    # =========================================================================
    # POST /speed-battle - Start Battle Tests
    # =========================================================================

    @patch('rq.Queue')
    @patch('redis.Redis')
    @patch('leads.routes.SpeedBattle')
    def test_start_battle_returns_battle_uid(self, mock_battle_class, mock_redis, mock_queue, client):
        """Test starting a battle returns battle_uid and redirect_url"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle_class.create.return_value = mock_battle

        mock_queue_instance = Mock()
        mock_queue.return_value = mock_queue_instance

        response = client.post('/speed-battle',
            json={
                'challenger_url': 'https://mystore.com',
                'opponent_url': 'https://competitor.com'
            },
            content_type='application/json')

        assert response.status_code == 200
        data = response.get_json()
        assert 'battle_uid' in data
        assert data['battle_uid'] == 'abc12345'
        assert 'redirect_url' in data

    def test_start_battle_missing_urls_returns_400(self, client):
        """Test starting a battle with missing URLs returns 400"""
        response = client.post('/speed-battle',
            json={'challenger_url': 'https://mystore.com'},
            content_type='application/json')

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_start_battle_invalid_url_returns_400(self, client):
        """Test starting a battle with invalid URL returns 400"""
        response = client.post('/speed-battle',
            json={
                'challenger_url': 'not-a-url',
                'opponent_url': 'https://competitor.com'
            },
            content_type='application/json')

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_start_battle_same_domain_returns_400(self, client):
        """Test starting a battle with same domain returns 400"""
        response = client.post('/speed-battle',
            json={
                'challenger_url': 'https://example.com/page1',
                'opponent_url': 'https://example.com/page2'
            },
            content_type='application/json')

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    @patch('rq.Queue')
    @patch('redis.Redis')
    @patch('leads.routes.SpeedBattle')
    def test_start_battle_with_ref_links_referrer(self, mock_battle_class, mock_redis, mock_queue, client):
        """Test starting battle with ref param links to referrer battle"""
        # Setup referrer battle
        mock_referrer = Mock()
        mock_referrer.id = 10
        mock_referrer.battle_uid = 'ref12345'

        # Setup new battle
        mock_battle = Mock()
        mock_battle.id = 2
        mock_battle.battle_uid = 'new12345'

        mock_battle_class.get_by_uid.return_value = mock_referrer
        mock_battle_class.create.return_value = mock_battle

        mock_queue_instance = Mock()
        mock_queue.return_value = mock_queue_instance

        response = client.post('/speed-battle?ref=ref12345',
            json={
                'challenger_url': 'https://mystore.com',
                'opponent_url': 'https://competitor.com'
            },
            content_type='application/json')

        assert response.status_code == 200
        # Verify referrer_battle_id was passed to create
        mock_battle_class.create.assert_called_once()
        call_kwargs = mock_battle_class.create.call_args
        # Check referrer_battle_id is in the call
        assert call_kwargs[1].get('referrer_battle_id') == 10 or call_kwargs[0][-1] == 10

    @patch('rq.Queue')
    @patch('redis.Redis')
    @patch('leads.routes.SpeedBattle')
    def test_start_battle_queues_job(self, mock_battle_class, mock_redis, mock_queue, client):
        """Test starting battle queues run_speed_battle job"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle_class.create.return_value = mock_battle

        mock_queue_instance = Mock()
        mock_queue.return_value = mock_queue_instance

        response = client.post('/speed-battle',
            json={
                'challenger_url': 'https://mystore.com',
                'opponent_url': 'https://competitor.com'
            },
            content_type='application/json')

        assert response.status_code == 200
        mock_queue_instance.enqueue.assert_called_once()

    # =========================================================================
    # GET /speed-battle/<battle_uid> - Results Page Tests
    # =========================================================================

    @patch('leads.routes.SiteScan')
    @patch('leads.routes.SpeedBattle')
    def test_results_page_returns_200(self, mock_battle_class, mock_sitescan_class, client):
        """Test results page returns 200 for existing battle"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle.status = 'completed'
        mock_battle.challenger_url = 'https://mystore.com'
        mock_battle.opponent_url = 'https://competitor.com'
        mock_battle.challenger_scan_id = 100
        mock_battle.opponent_scan_id = 101
        mock_battle.challenger_score = 85
        mock_battle.opponent_score = 65
        mock_battle.winner = 'challenger'
        mock_battle.margin = 20
        mock_battle.email = None
        mock_battle_class.get_by_uid.return_value = mock_battle

        mock_scan = Mock()
        mock_scan.pagespeed_data = None
        mock_sitescan_class.get_by_id.return_value = mock_scan

        response = client.get('/speed-battle/abc12345')

        assert response.status_code == 200

    @patch('leads.routes.SpeedBattle')
    def test_results_page_returns_404_for_missing_battle(self, mock_battle_class, client):
        """Test results page returns 404 for non-existent battle"""
        mock_battle_class.get_by_uid.return_value = None

        response = client.get('/speed-battle/nonexistent')

        assert response.status_code == 404

    @patch('leads.routes.SpeedBattle')
    def test_results_page_pending_battle(self, mock_battle_class, client):
        """Test results page renders for pending battle"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle.status = 'pending'
        mock_battle.challenger_url = 'https://mystore.com'
        mock_battle.opponent_url = 'https://competitor.com'
        mock_battle.challenger_scan_id = None
        mock_battle.opponent_scan_id = None
        mock_battle.email = None
        mock_battle_class.get_by_uid.return_value = mock_battle

        response = client.get('/speed-battle/abc12345')

        assert response.status_code == 200

    # =========================================================================
    # GET /speed-battle/<battle_uid>/status - Status Polling Tests
    # =========================================================================

    @patch('leads.routes.SpeedBattle')
    def test_status_endpoint_returns_json(self, mock_battle_class, client):
        """Test status endpoint returns JSON"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle.status = 'scanning'
        mock_battle.to_dict.return_value = {
            'id': 1,
            'battle_uid': 'abc12345',
            'status': 'scanning',
            'challenger_url': 'https://mystore.com',
            'opponent_url': 'https://competitor.com'
        }
        mock_battle_class.get_by_uid.return_value = mock_battle

        response = client.get('/speed-battle/abc12345/status')

        assert response.status_code == 200
        data = response.get_json()
        assert 'status' in data
        assert data['status'] == 'scanning'

    @patch('leads.routes.SpeedBattle')
    def test_status_endpoint_returns_404_for_missing(self, mock_battle_class, client):
        """Test status endpoint returns 404 for missing battle"""
        mock_battle_class.get_by_uid.return_value = None

        response = client.get('/speed-battle/nonexistent/status')

        assert response.status_code == 404

    # =========================================================================
    # POST /speed-battle/<battle_uid>/unlock - Email Capture Tests
    # =========================================================================

    @patch('rq.Queue')
    @patch('redis.Redis')
    @patch('leads.routes.SpeedBattle')
    def test_unlock_endpoint_captures_email(self, mock_battle_class, mock_redis, mock_queue, client):
        """Test unlock endpoint captures email"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle.status = 'completed'
        mock_battle.get_email_segment.return_value = 'won_dominant'
        mock_battle_class.get_by_uid.return_value = mock_battle

        mock_queue_instance = Mock()
        mock_queue.return_value = mock_queue_instance

        response = client.post('/speed-battle/abc12345/unlock',
            json={'email': 'test@example.com'},
            content_type='application/json')

        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True
        assert 'segment' in data
        mock_battle.set_email.assert_called_once_with('test@example.com')

    @patch('leads.routes.SpeedBattle')
    def test_unlock_endpoint_validates_email(self, mock_battle_class, client):
        """Test unlock endpoint validates email format"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle_class.get_by_uid.return_value = mock_battle

        response = client.post('/speed-battle/abc12345/unlock',
            json={'email': 'not-an-email'},
            content_type='application/json')

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    @patch('leads.routes.SpeedBattle')
    def test_unlock_endpoint_returns_404_for_missing(self, mock_battle_class, client):
        """Test unlock endpoint returns 404 for missing battle"""
        mock_battle_class.get_by_uid.return_value = None

        response = client.post('/speed-battle/nonexistent/unlock',
            json={'email': 'test@example.com'},
            content_type='application/json')

        assert response.status_code == 404

    @patch('rq.Queue')
    @patch('redis.Redis')
    @patch('leads.routes.SpeedBattle')
    def test_unlock_endpoint_queues_email_job(self, mock_battle_class, mock_redis, mock_queue, client):
        """Test unlock endpoint queues send_battle_report_email job"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle.status = 'completed'
        mock_battle.get_email_segment.return_value = 'won_dominant'
        mock_battle_class.get_by_uid.return_value = mock_battle

        mock_queue_instance = Mock()
        mock_queue.return_value = mock_queue_instance

        response = client.post('/speed-battle/abc12345/unlock',
            json={'email': 'test@example.com'},
            content_type='application/json')

        assert response.status_code == 200
        mock_queue_instance.enqueue.assert_called_once()

    # =========================================================================
    # POST /speed-battle/<battle_uid>/share - Share Tracking Tests
    # =========================================================================

    @patch('leads.routes.SpeedBattle')
    def test_share_endpoint_tracks_clicks(self, mock_battle_class, client):
        """Test share endpoint tracks share clicks"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle_class.get_by_uid.return_value = mock_battle

        response = client.post('/speed-battle/abc12345/share',
            json={'platform': 'twitter'},
            content_type='application/json')

        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True
        mock_battle.increment_share_click.assert_called_once_with('twitter')

    @patch('leads.routes.SpeedBattle')
    def test_share_endpoint_validates_platform(self, mock_battle_class, client):
        """Test share endpoint validates platform"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle.increment_share_click.side_effect = ValueError("Invalid platform")
        mock_battle_class.get_by_uid.return_value = mock_battle

        response = client.post('/speed-battle/abc12345/share',
            json={'platform': 'invalid'},
            content_type='application/json')

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    @patch('leads.routes.SpeedBattle')
    def test_share_endpoint_returns_404_for_missing(self, mock_battle_class, client):
        """Test share endpoint returns 404 for missing battle"""
        mock_battle_class.get_by_uid.return_value = None

        response = client.post('/speed-battle/nonexistent/share',
            json={'platform': 'twitter'},
            content_type='application/json')

        assert response.status_code == 404

    @patch('leads.routes.SpeedBattle')
    def test_share_endpoint_all_platforms(self, mock_battle_class, client):
        """Test share endpoint works for all valid platforms"""
        valid_platforms = ['twitter', 'facebook', 'linkedin', 'copy']

        for platform in valid_platforms:
            mock_battle = Mock()
            mock_battle.id = 1
            mock_battle.battle_uid = 'abc12345'
            mock_battle_class.get_by_uid.return_value = mock_battle

            response = client.post('/speed-battle/abc12345/share',
                json={'platform': platform},
                content_type='application/json')

            assert response.status_code == 200
            data = response.get_json()
            assert data['success'] is True

    # =========================================================================
    # Form Data Tests (non-JSON)
    # =========================================================================

    @patch('rq.Queue')
    @patch('redis.Redis')
    @patch('leads.routes.SpeedBattle')
    def test_start_battle_with_form_data(self, mock_battle_class, mock_redis, mock_queue, client):
        """Test starting a battle with form data instead of JSON"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle_class.create.return_value = mock_battle

        mock_queue_instance = Mock()
        mock_queue.return_value = mock_queue_instance

        response = client.post('/speed-battle',
            data={
                'challenger_url': 'https://mystore.com',
                'opponent_url': 'https://competitor.com'
            })

        assert response.status_code == 200

    @patch('rq.Queue')
    @patch('redis.Redis')
    @patch('leads.routes.SpeedBattle')
    def test_unlock_with_form_data(self, mock_battle_class, mock_redis, mock_queue, client):
        """Test unlock endpoint with form data instead of JSON"""
        mock_battle = Mock()
        mock_battle.id = 1
        mock_battle.battle_uid = 'abc12345'
        mock_battle.status = 'completed'
        mock_battle.get_email_segment.return_value = 'won_close'
        mock_battle_class.get_by_uid.return_value = mock_battle

        mock_queue_instance = Mock()
        mock_queue.return_value = mock_queue_instance

        response = client.post('/speed-battle/abc12345/unlock',
            data={'email': 'test@example.com'})

        assert response.status_code == 200
