"""A service-failed hosted image must not fail the whole turn."""

from kollabor_ai.api_communication_service import APICommunicationService


def _item(item_id, status, result=None):
    return {"type": "image_generation_call", "id": item_id, "status": status, "result": result}


def test_failed_image_item_is_not_expected_as_artifact():
    raw = {"output": [_item("a", "completed", "x"), _item("b", "failed"), _item("c", "completed", "y")]}
    assert APICommunicationService._count_generated_image_items(raw) == 2


def test_completed_image_without_result_still_counted():
    raw = {"output": [_item("a", "completed")]}
    assert APICommunicationService._count_generated_image_items(raw) == 1


def test_only_failed_images_with_progress_do_not_fail_the_turn():
    service = APICommunicationService.__new__(APICommunicationService)
    service.last_generated_images = []
    service._generated_image_store = None
    raw = {"output": [_item("a", "failed"), _item("b", "failed")]}
    service._verify_generated_image_artifacts(raw, image_generation_signaled=True)


def test_progress_with_no_image_item_still_fails():
    import pytest

    from kollabor_ai.providers.errors import ProviderError

    service = APICommunicationService.__new__(APICommunicationService)
    service.last_generated_images = []
    service._generated_image_store = None
    service._provider = None
    with pytest.raises(ProviderError):
        service._verify_generated_image_artifacts({"output": []}, image_generation_signaled=True)
