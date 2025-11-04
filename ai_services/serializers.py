from rest_framework import serializers

class CharacterIdentifierRequestSerializer(serializers.Serializer):
    """
    Serializer to validate the request body for the Character Identifier endpoint.
    """
    input_file_path = serializers.CharField(
        required=True,
        help_text="Path to the narrative_blueprint.json file, relative to the project's 'resource' directory."
    )
    characters_to_analyze = serializers.ListField(
        child=serializers.CharField(),
        required=True,
        min_length=1,
        help_text="A list of character names to analyze."
    )
    lang = serializers.ChoiceField(
        choices=['zh', 'en'],
        default='zh',
        help_text="Language for prompts and localization."
    )
    model = serializers.CharField(
        default='gemini-1.5-flash-latest',
        help_text="The Gemini model to use for inference."
    )
    temp = serializers.FloatField(
        default=0.1,
        min_value=0.0,
        max_value=2.0,
        help_text="Temperature for the AI model."
    )

    def validate_input_file_path(self, value):
        """
        You can add custom validation to check if the file actually exists,
        but for now, we'll just ensure it's a string.
        """
        # A more robust implementation would check for path traversal attacks.
        if ".." in value:
            raise serializers.ValidationError("Relative paths with '..' are not allowed.")
        return value