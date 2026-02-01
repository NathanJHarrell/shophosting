"""
Tests for webapp/leads/models.py - SpeedBattle model
Following TDD: tests written first, implementation follows
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
import string


class TestSpeedBattleModel:
    """Test SpeedBattle model methods"""

    # =========================================================================
    # Initialization Tests
    # =========================================================================

    def test_init_with_defaults(self):
        """Test SpeedBattle initialization with defaults"""
        from leads.models import SpeedBattle
        battle = SpeedBattle()

        assert battle.id is None
        assert battle.battle_uid is None
        assert battle.challenger_url is None
        assert battle.challenger_scan_id is None
        assert battle.challenger_score is None
        assert battle.opponent_url is None
        assert battle.opponent_scan_id is None
        assert battle.opponent_score is None
        assert battle.winner is None
        assert battle.margin is None
        assert battle.email is None
        assert battle.email_segment is None
        assert battle.referrer_battle_id is None
        assert battle.share_clicks_twitter == 0
        assert battle.share_clicks_facebook == 0
        assert battle.share_clicks_linkedin == 0
        assert battle.share_clicks_copy == 0
        assert battle.status == 'pending'
        assert battle.error_message is None
        assert battle.ip_address is None
        assert battle.created_at is not None
        assert battle.updated_at is not None
        assert battle.completed_at is None

    def test_init_with_values(self):
        """Test SpeedBattle initialization with provided values"""
        from leads.models import SpeedBattle
        created = datetime(2026, 1, 15, 10, 30, 0)
        updated = datetime(2026, 1, 15, 10, 35, 0)
        completed = datetime(2026, 1, 15, 10, 35, 0)

        battle = SpeedBattle(
            id=42,
            battle_uid='abc12345',
            challenger_url='https://store1.com',
            challenger_scan_id=100,
            challenger_score=85,
            opponent_url='https://store2.com',
            opponent_scan_id=101,
            opponent_score=65,
            winner='challenger',
            margin=20,
            email='test@example.com',
            email_segment='won_dominant',
            referrer_battle_id=10,
            share_clicks_twitter=5,
            share_clicks_facebook=3,
            share_clicks_linkedin=2,
            share_clicks_copy=8,
            status='completed',
            error_message=None,
            ip_address='192.168.1.1',
            created_at=created,
            updated_at=updated,
            completed_at=completed
        )

        assert battle.id == 42
        assert battle.battle_uid == 'abc12345'
        assert battle.challenger_url == 'https://store1.com'
        assert battle.challenger_scan_id == 100
        assert battle.challenger_score == 85
        assert battle.opponent_url == 'https://store2.com'
        assert battle.opponent_scan_id == 101
        assert battle.opponent_score == 65
        assert battle.winner == 'challenger'
        assert battle.margin == 20
        assert battle.email == 'test@example.com'
        assert battle.email_segment == 'won_dominant'
        assert battle.referrer_battle_id == 10
        assert battle.share_clicks_twitter == 5
        assert battle.share_clicks_facebook == 3
        assert battle.share_clicks_linkedin == 2
        assert battle.share_clicks_copy == 8
        assert battle.status == 'completed'
        assert battle.error_message is None
        assert battle.ip_address == '192.168.1.1'
        assert battle.created_at == created
        assert battle.updated_at == updated
        assert battle.completed_at == completed

    # =========================================================================
    # Class Constants Tests
    # =========================================================================

    def test_statuses_constant(self):
        """Test STATUSES class constant"""
        from leads.models import SpeedBattle

        assert SpeedBattle.STATUSES == ['pending', 'scanning', 'completed', 'failed']

    def test_winners_constant(self):
        """Test WINNERS class constant"""
        from leads.models import SpeedBattle

        assert SpeedBattle.WINNERS == ['challenger', 'opponent', 'tie']

    def test_email_segments_constant(self):
        """Test EMAIL_SEGMENTS class constant"""
        from leads.models import SpeedBattle

        assert SpeedBattle.EMAIL_SEGMENTS == ['won_dominant', 'won_close', 'lost_close', 'lost_dominant']

    # =========================================================================
    # generate_battle_uid Tests
    # =========================================================================

    def test_generate_battle_uid_format(self):
        """Test generate_battle_uid returns 8-char alphanumeric string"""
        from leads.models import SpeedBattle

        uid = SpeedBattle.generate_battle_uid()

        assert len(uid) == 8
        assert uid.isalnum()
        # Should only contain letters and digits
        valid_chars = set(string.ascii_letters + string.digits)
        assert all(c in valid_chars for c in uid)

    def test_generate_battle_uid_uniqueness(self):
        """Test generate_battle_uid produces unique values (100 calls)"""
        from leads.models import SpeedBattle

        uids = [SpeedBattle.generate_battle_uid() for _ in range(100)]

        # All 100 should be unique
        assert len(set(uids)) == 100

    # =========================================================================
    # determine_winner Tests
    # =========================================================================

    def test_determine_winner_challenger_wins(self):
        """Test determine_winner when challenger has higher score"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(challenger_score=85, opponent_score=65)

        battle.determine_winner()

        assert battle.winner == 'challenger'
        assert battle.margin == 20

    def test_determine_winner_opponent_wins(self):
        """Test determine_winner when opponent has higher score"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(challenger_score=50, opponent_score=75)

        battle.determine_winner()

        assert battle.winner == 'opponent'
        assert battle.margin == 25

    def test_determine_winner_tie(self):
        """Test determine_winner when scores are equal"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(challenger_score=70, opponent_score=70)

        battle.determine_winner()

        assert battle.winner == 'tie'
        assert battle.margin == 0

    def test_determine_winner_close_margin(self):
        """Test determine_winner with small margin"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(challenger_score=72, opponent_score=70)

        battle.determine_winner()

        assert battle.winner == 'challenger'
        assert battle.margin == 2

    # =========================================================================
    # get_email_segment Tests
    # =========================================================================

    def test_get_email_segment_won_dominant(self):
        """Test get_email_segment when challenger won by 20+"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(
            challenger_score=90,
            opponent_score=65,
            winner='challenger',
            margin=25
        )

        segment = battle.get_email_segment()

        assert segment == 'won_dominant'

    def test_get_email_segment_won_close(self):
        """Test get_email_segment when challenger won by less than 20"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(
            challenger_score=75,
            opponent_score=65,
            winner='challenger',
            margin=10
        )

        segment = battle.get_email_segment()

        assert segment == 'won_close'

    def test_get_email_segment_lost_close(self):
        """Test get_email_segment when opponent won by less than 20"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(
            challenger_score=60,
            opponent_score=72,
            winner='opponent',
            margin=12
        )

        segment = battle.get_email_segment()

        assert segment == 'lost_close'

    def test_get_email_segment_lost_dominant(self):
        """Test get_email_segment when opponent won by 20+"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(
            challenger_score=50,
            opponent_score=80,
            winner='opponent',
            margin=30
        )

        segment = battle.get_email_segment()

        assert segment == 'lost_dominant'

    def test_get_email_segment_tie(self):
        """Test get_email_segment returns None for tie"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(
            challenger_score=70,
            opponent_score=70,
            winner='tie',
            margin=0
        )

        segment = battle.get_email_segment()

        # Tie could be None or 'won_close' based on implementation
        # A tie means they didn't lose, so won_close makes sense
        assert segment in [None, 'won_close']

    def test_get_email_segment_boundary_20(self):
        """Test get_email_segment at exactly 20 margin (should be dominant)"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(
            challenger_score=85,
            opponent_score=65,
            winner='challenger',
            margin=20
        )

        segment = battle.get_email_segment()

        assert segment == 'won_dominant'

    def test_get_email_segment_boundary_19(self):
        """Test get_email_segment at exactly 19 margin (should be close)"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(
            challenger_score=84,
            opponent_score=65,
            winner='challenger',
            margin=19
        )

        segment = battle.get_email_segment()

        assert segment == 'won_close'

    # =========================================================================
    # to_dict Tests
    # =========================================================================

    def test_to_dict_returns_expected_keys(self):
        """Test to_dict returns dict with expected keys"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(
            id=1,
            battle_uid='test1234',
            challenger_url='https://store1.com',
            opponent_url='https://store2.com',
            status='completed'
        )

        result = battle.to_dict()

        assert isinstance(result, dict)
        expected_keys = [
            'id', 'battle_uid', 'challenger_url', 'challenger_scan_id',
            'challenger_score', 'opponent_url', 'opponent_scan_id',
            'opponent_score', 'winner', 'margin', 'email', 'email_segment',
            'referrer_battle_id', 'share_clicks_twitter', 'share_clicks_facebook',
            'share_clicks_linkedin', 'share_clicks_copy', 'status',
            'error_message', 'ip_address', 'created_at', 'updated_at', 'completed_at'
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_to_dict_values_match(self):
        """Test to_dict returns correct values"""
        from leads.models import SpeedBattle
        battle = SpeedBattle(
            id=42,
            battle_uid='xyz98765',
            challenger_url='https://mystore.com',
            challenger_score=90,
            opponent_url='https://competitor.com',
            opponent_score=70,
            winner='challenger',
            margin=20,
            status='completed'
        )

        result = battle.to_dict()

        assert result['id'] == 42
        assert result['battle_uid'] == 'xyz98765'
        assert result['challenger_url'] == 'https://mystore.com'
        assert result['challenger_score'] == 90
        assert result['opponent_url'] == 'https://competitor.com'
        assert result['opponent_score'] == 70
        assert result['winner'] == 'challenger'
        assert result['margin'] == 20
        assert result['status'] == 'completed'

    # =========================================================================
    # Database Method Tests (mocked)
    # =========================================================================

    @patch('leads.models.get_db_connection')
    def test_get_by_id_found(self, mock_get_conn):
        """Test get_by_id returns SpeedBattle when found"""
        from leads.models import SpeedBattle

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            'id': 1,
            'battle_uid': 'test1234',
            'challenger_url': 'https://store1.com',
            'opponent_url': 'https://store2.com',
            'status': 'pending'
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = SpeedBattle.get_by_id(1)

        assert result is not None
        assert isinstance(result, SpeedBattle)
        assert result.id == 1
        assert result.battle_uid == 'test1234'
        mock_cursor.execute.assert_called_once()
        mock_cursor.close.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('leads.models.get_db_connection')
    def test_get_by_id_not_found(self, mock_get_conn):
        """Test get_by_id returns None when not found"""
        from leads.models import SpeedBattle

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = SpeedBattle.get_by_id(999)

        assert result is None

    @patch('leads.models.get_db_connection')
    def test_get_by_uid_found(self, mock_get_conn):
        """Test get_by_uid returns SpeedBattle when found"""
        from leads.models import SpeedBattle

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            'id': 5,
            'battle_uid': 'abc12345',
            'challenger_url': 'https://store.com',
            'opponent_url': 'https://rival.com',
            'status': 'completed'
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = SpeedBattle.get_by_uid('abc12345')

        assert result is not None
        assert result.battle_uid == 'abc12345'

    @patch('leads.models.get_db_connection')
    def test_get_by_uid_not_found(self, mock_get_conn):
        """Test get_by_uid returns None when not found"""
        from leads.models import SpeedBattle

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = SpeedBattle.get_by_uid('nonexist')

        assert result is None

    @patch('leads.models.get_db_connection')
    def test_update_status(self, mock_get_conn):
        """Test update_status updates DB and local object"""
        from leads.models import SpeedBattle

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        battle = SpeedBattle(id=1, status='pending')
        result = battle.update_status('scanning')

        assert result is True
        assert battle.status == 'scanning'
        mock_cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @patch('leads.models.get_db_connection')
    def test_update_status_with_error(self, mock_get_conn):
        """Test update_status with error message"""
        from leads.models import SpeedBattle

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        battle = SpeedBattle(id=1, status='scanning')
        result = battle.update_status('failed', error_message='PageSpeed API error')

        assert result is True
        assert battle.status == 'failed'
        assert battle.error_message == 'PageSpeed API error'

    @patch('leads.models.get_db_connection')
    def test_increment_share_click(self, mock_get_conn):
        """Test increment_share_click increments counter"""
        from leads.models import SpeedBattle

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        battle = SpeedBattle(id=1, share_clicks_twitter=5)
        result = battle.increment_share_click('twitter')

        assert result is True
        assert battle.share_clicks_twitter == 6
        mock_cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @patch('leads.models.get_db_connection')
    def test_increment_share_click_facebook(self, mock_get_conn):
        """Test increment_share_click for facebook"""
        from leads.models import SpeedBattle

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        battle = SpeedBattle(id=1, share_clicks_facebook=2)
        result = battle.increment_share_click('facebook')

        assert result is True
        assert battle.share_clicks_facebook == 3

    def test_increment_share_click_invalid_platform(self):
        """Test increment_share_click with invalid platform"""
        from leads.models import SpeedBattle

        battle = SpeedBattle(id=1)

        with pytest.raises(ValueError):
            battle.increment_share_click('instagram')
