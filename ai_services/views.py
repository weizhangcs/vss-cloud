# In: the_same_app_as_views/views.py

import logging
from pathlib import Path

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

# --- Import your decoupled services and dependencies ---
from ai_services.analysis.character.character_identifier import CharacterIdentifier
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.common.gemini.cost_calculator_v2 import CostCalculator

# --- Import your new serializer ---
from .serializers import CharacterIdentifierRequestSerializer

# Get a logger instance
logger = logging.getLogger(__name__)


class CharacterIdentifierAPIView(APIView):
    """
    An API endpoint to trigger the Character Identification service.
    """

    def post(self, request, *args, **kwargs):
        # 1. Validate the incoming request data
        serializer = CharacterIdentifierRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data

        try:
            # ==================== Composition Root (Django Version) ====================
            # 2. Assemble dependencies using Django's settings

            # Create GeminiProcessor
            gemini_processor = GeminiProcessor(
                api_key=settings.GOOGLE_API_KEY,
                logger=logger,
                debug_mode=settings.DEBUG,
                debug_dir=settings.SHARED_OUTPUT_ROOT / "character_facts_api_debug"
            )

            # Create CostCalculator
            cost_calculator = CostCalculator(
                pricing_data=settings.GEMINI_PRICING,
                usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
            )

            # Define required paths using settings.SHARED_RESOURCE_ROOT
            service_name = CharacterIdentifier.SERVICE_NAME
            prompts_dir = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'prompts'
            localization_path = settings.SHARED_RESOURCE_ROOT / "localization" / "analysis" / f"{service_name}.json"
            schema_path = settings.SHARED_RESOURCE_ROOT / "metadata" / "fact_attributes.json"

            # Construct the full path for the input file
            input_file_full_path = settings.SHARED_RESOURCE_ROOT / validated_data['input_file_path']

            if not input_file_full_path.is_file():
                return Response(
                    {"error": f"Input file not found at: {input_file_full_path}"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # 3. Instantiate the service, injecting all dependencies
            identifier_service = CharacterIdentifier(
                gemini_processor=gemini_processor,
                cost_calculator=cost_calculator,
                prompts_dir=prompts_dir,
                localization_path=localization_path,
                schema_path=schema_path,
                logger=logger,
                base_path=settings.SHARED_OUTPUT_ROOT / "character_facts_api"
            )
            # ========================================================================

            # 4. Execute the service with validated data
            result_data = identifier_service.execute(
                enhanced_script_path=input_file_full_path,
                characters_to_analyze=validated_data['characters_to_analyze'],
                lang=validated_data['lang'],
                model=validated_data['model'],
                temp=validated_data['temp'],
                debug=settings.DEBUG
            )

            # 5. Return the result
            return Response(result_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error during character identification: {e}", exc_info=True)
            return Response(
                {"error": "An internal server error occurred.", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )