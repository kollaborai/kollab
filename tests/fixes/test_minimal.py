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

    msg_id = manager.add_message("user", "Test message")
    print(f"✅ Message added with ID: {msg_id}")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback

    traceback.print_exc()
