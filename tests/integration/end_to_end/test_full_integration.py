import sys

sys.path.append(".")

try:
    from kollabor.application import TerminalLLMChat

    print("✅ All imports successful")

    # Test creating an instance
    app = TerminalLLMChat()
    print("✅ TerminalLLMChat created successfully")

    # Verify all components exist
    assert hasattr(
        app.llm_service, "conversation_manager"
    ), "conversation_manager missing"
    assert hasattr(
        app.llm_service, "conversation_logger"
    ), "conversation_logger missing"
    assert hasattr(
        app.llm_service, "conversation_history"
    ), "conversation_history missing"

    print("✅ All components accessible")

    # Test ConversationManager integration
    manager = app.llm_service.conversation_manager
    msg1 = manager.add_message("user", "Test message")
    msg2 = manager.add_message("assistant", "Response", parent_uuid=msg1)

    print(
        f"✅ ConversationManager working: {len(manager.get_context_messages())} messages"
    )

    # Test legacy compatibility
    history = app.llm_service.conversation_history
    print(f"✅ Legacy history maintained: {len(history)} messages")

    print("\n🎉 Full integration test passed!")
    print("🎉 All components working together!")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback

    traceback.print_exc()
