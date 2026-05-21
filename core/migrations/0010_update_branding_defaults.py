from django.db import migrations


def update_branding_defaults(apps, schema_editor):
    BrandingConfig = apps.get_model('core', 'BrandingConfig')
    obj, _ = BrandingConfig.objects.get_or_create(pk=1)
    obj.company_name = 'TS Money'
    obj.tagline = 'invest like you mean it'
    obj.primary_color = '#1A3F8B'
    obj.secondary_color = '#2D5FA0'
    obj.success_color = '#2FA02A'
    obj.danger_color = '#C0392B'
    obj.navbar_bg_color = '#1A3F8B'
    obj.navbar_text_color = '#FFFFFF'
    obj.footer_bg_color = '#1A3F8B'
    obj.footer_text = '\u00a9 TS Money \u2014 invest like you mean it'
    obj.show_logo = True
    obj.show_tagline = False  # tagline now lives in the banner image
    obj.save()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_account_category_account_owner'),
    ]

    operations = [
        migrations.RunPython(update_branding_defaults, migrations.RunPython.noop),
    ]
