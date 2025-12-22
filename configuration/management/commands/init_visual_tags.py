from django.core.management.base import BaseCommand
from configuration.models import TagDefinition
from configuration.tag_manager import TagManager


class Command(BaseCommand):
    help = 'Initialize visual mood tags (V4.1 Multi-Lang) and sync to Redis'

    def handle(self, *args, **options):
        self.stdout.write("SEEDING Tag Definitions (Multi-Lang)...")

        # 格式: (Canonical, Label_ZH, Label_EN, [Mixed Synonyms])
        seeds = [
            ("warm", "温暖", "Warm", ["romantic", "cozy", "sunny", "golden", "温馨", "暖色调", "柔和"]),
            ("cold", "寒冷", "Cold", ["blue", "chilly", "steely", "depressing", "clinical", "清冷", "冷冽"]),
            ("dark", "阴暗", "Dark", ["dim", "shadowy", "night", "obscure", "low key", "昏暗", "压抑"]),
            ("tense", "紧张", "Tense", ["suspenseful", "nervous", "dangerous", "uneasy", "紧迫", "危机感"]),
            ("bright", "明亮", "Bright", ["cheerful", "vibrant", "high-key", "energetic", "欢快", "鲜艳"]),
            ("melancholic", "忧郁", "Melancholic", ["sad", "lonely", "gloomy", "悲伤", "孤独"]),
        ]

        created_count = 0
        updated_count = 0

        for name, label_zh, label_en, syns in seeds:
            obj, created = TagDefinition.objects.update_or_create(
                name=name,
                defaults={
                    'category': 'visual_mood',
                    'label_zh': label_zh,
                    'label_en': label_en,
                    'synonyms': syns,
                    'is_active': True
                }
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(f"Database seeded. Created {created_count}, Updated {updated_count}.")

        self.stdout.write("SYNCING to Redis...")
        TagManager.sync_to_redis('visual_mood')

        self.stdout.write(self.style.SUCCESS("✅ Visual Tags (V4.1) Initialized & Synced!"))