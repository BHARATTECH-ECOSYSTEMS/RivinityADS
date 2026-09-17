"""
Tests for ML models.
"""

import pytest
import torch

from liteads.ml_engine.models.deepfm import DeepFM



class TestDeepFM:
    """Tests for DeepFM model."""

    @pytest.fixture
    def model(self) -> DeepFM:
        """Create DeepFM model for testing."""
        return DeepFM(
            sparse_feature_dims=[100, 50, 200],
            sparse_embedding_dims=8,
            dense_feature_dim=5,
            fm_k=8,
            dnn_hidden_units=[64, 32],
            dnn_dropout=0.1,
        )

    def test_forward(self, model: DeepFM) -> None:
        """Test DeepFM forward pass."""
        batch_size = 32

        sparse_features = torch.randint(0, 50, (batch_size, 3))
        dense_features = torch.randn(batch_size, 5)

        outputs = model(sparse_features, dense_features)

        assert "ctr" in outputs
        assert outputs["ctr"].shape == (batch_size,)
        # Output should be probabilities
        assert (outputs["ctr"] >= 0).all()
        assert (outputs["ctr"] <= 1).all()

    def test_without_dense(self) -> None:
        """Test DeepFM without dense features."""
        model = DeepFM(
            sparse_feature_dims=[100, 50],
            sparse_embedding_dims=8,
            dense_feature_dim=0,
            dnn_hidden_units=[32, 16],
        )

        sparse_features = torch.randint(0, 50, (16, 2))

        outputs = model(sparse_features, None)

        assert outputs["ctr"].shape == (16,)

    def test_without_fm(self) -> None:
        """Test DeepFM without FM component."""
        model = DeepFM(
            sparse_feature_dims=[100],
            sparse_embedding_dims=8,
            dense_feature_dim=3,
            dnn_hidden_units=[32],
        )

        sparse_features = torch.randint(0, 50, (16, 1))
        dense_features = torch.randn(16, 3)

        outputs = model(sparse_features, dense_features)

        assert outputs["ctr"].shape == (16,)

    def test_without_dnn(self) -> None:
        """Test DeepFM without DNN component (FM only)."""
        model = DeepFM(
            sparse_feature_dims=[100, 50],
            sparse_embedding_dims=8,
            dense_feature_dim=2,
        )

        sparse_features = torch.randint(0, 50, (16, 2))
        dense_features = torch.randn(16, 2)

        outputs = model(sparse_features, dense_features)

        assert outputs["ctr"].shape == (16,)

    def test_regularization_loss(self, model: DeepFM) -> None:
        """Test regularization loss calculation."""
        # Model needs to have non-zero reg coefficients
        model.l2_reg_embedding = 0.01
        model.l2_reg_dnn = 0.01

        reg_loss = model.get_regularization_loss()

        assert reg_loss > 0

    def test_gradient_flow(self, model: DeepFM) -> None:
        """Test gradients flow correctly."""
        sparse_features = torch.randint(0, 50, (8, 3))
        dense_features = torch.randn(8, 5)

        outputs = model(sparse_features, dense_features)
        loss = outputs["ctr"].mean()
        loss.backward()

        # Check gradients exist
        for param in model.parameters():
            if param.requires_grad:
                assert param.grad is not None



