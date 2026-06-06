import os
import logging
from openai import OpenAI

# Initialize logger for this module
logger = logging.getLogger(__name__)

class OpenAIService:
    """
    Handles interactions with the OpenAI API for text and image generation.
    """
    
    def __init__(self, api_key, image_model="dall-e-3", image_quality="standard"):
        """
        Initializes the OpenAI client with model and quality settings.
        """
        self.client = OpenAI(api_key=api_key)
        self.text_model = "gpt-4o"
        self.image_model = image_model
        self.image_quality = image_quality

    async def generate_event_promo(self, event_details, system_prompt):
        """
        Generates a Telegram post and a DALL-E prompt based on event details.
        """
        try:
            response = self.client.chat.completions.create(
                model=self.text_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Event Details: {event_details}"}
                ],
                response_format={ "type": "json_object" } # Force JSON output
            )
            return response.choices[0].message.content
        except Exception as e:
            if "insufficient_quota" in str(e).lower():
                logger.warning("⚠️  OpenAI Error: Insufficient Quota. Draft and flyer generation aborted.")
                return "QUOTA_EXCEEDED"
            logger.error(f"❌ OpenAI Text Generation Error: {e}")
            return None

    async def refine_event_promo(self, event_details, current_draft, instructions, system_prompt):
        """
        Refines an existing draft based on Admin instructions.
        """
        try:
            user_content = (
                f"Original Event Details: {event_details}\n\n"
                f"Current Draft: {current_draft}\n\n"
                f"Admin Instructions: {instructions}"
            )
            
            response = self.client.chat.completions.create(
                model=self.text_model,
                messages=[
                    {"role": "system", "content": system_prompt + "\n\nYou are now refining a draft based on specific user feedback. Keep the tone consistent but apply the requested changes exactly."},
                    {"role": "user", "content": user_content}
                ],
                response_format={ "type": "json_object" }
            )
            return response.choices[0].message.content
        except Exception as e:
            if "insufficient_quota" in str(e).lower():
                logger.warning("⚠️  OpenAI Error: Insufficient Quota during refinement.")
                return "QUOTA_EXCEEDED"
            logger.error(f"❌ OpenAI Refinement Error: {e}")
            return None

    async def generate_image(self, image_prompt):
        """
        Generates a flyer image using the configured image model and quality.
        """
        try:
            response = self.client.images.generate(
                model=self.image_model,
                prompt=image_prompt,
                size="1024x1024",
                quality=self.image_quality,
                n=1,
            )
            return response.data[0].url
        except Exception as e:
            if "insufficient_quota" in str(e).lower():
                logger.warning("⚠️  OpenAI Error: Insufficient Quota for image generation.")
                return "QUOTA_EXCEEDED"
            logger.error(f"❌ OpenAI Image Generation Error: {e}")
            return None
