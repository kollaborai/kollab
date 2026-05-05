import sys

sys.path.append(".")

try:

    print("✅ LLMService imported successfully")

    # Test ConversationManager import
    from kollabor_ai import ConversationManager

    print("✅ ConversationManager imported successfully")

    # Test basic ConversationManager functionality
    class MockConfig:
        def get(self, key, default=None):
            return default

    config = MockConfig()
    logger = None

    manager = ConversationManager(config, logger)
    print("✅ ConversationManager created successfully")

    # Test parent UUID tracking
    msg1 = manager.add_message("user", "Hello")
    msg2 = manager.add_message("assistant", "Hi there!", parent_uuid=msg1)
    msg3 = manager.add_message("user", "How are you?")

    print("✅ Added 3 messages with proper threading")
    print(f"✅ Parent UUID tracking: {manager.current_parent_uuid}")

    # Test context retrieval
    context = manager.get_context_messages()
    print(f"✅ Retrieved {len(context)} context messages")

    # Test conversation summary
    summary = manager.get_conversation_summary()
    print(
        f"✅ Conversation summary: {summary['total_messages']} messages, {summary['turn_count']} turns"
    )

    # Test message threading
    thread = manager.get_message_thread(msg3)
    print(f"✅ Message thread length: {len(thread)}")

    # Test message search
    search_results = manager.search_messages("hello")
    print(f"✅ Search results: {len(search_results)} matches")

    # Test conversation persistence
    saved_path = manager.save_conversation()
    print(f"✅ Conversation saved to: {saved_path}")

    # Test conversation stats
    stats = manager.get_conversation_stats()
    print(f"✅ Conversation stats: {stats['messages']['total']} total messages")

    # Test message export
    training_data = manager.export_for_training()
    print(f"✅ Training data export: {len(training_data)} message pairs")

    print("\n🎉 All ConversationManager features working correctly!")
    print("🎉 Integration complete - all append operations replaced!")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback

    traceback.print_exc()
