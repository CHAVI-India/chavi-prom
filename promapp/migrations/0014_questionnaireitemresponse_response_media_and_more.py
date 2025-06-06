# Generated by Django 5.2.1 on 2025-06-07 09:07

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('promapp', '0013_alter_patientquestionnaire_display_questionnaire_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='questionnaireitemresponse',
            name='response_media',
            field=models.FileField(blank=True, help_text='The media response', null=True, upload_to='questionnaire_responses/'),
        ),
        migrations.AlterField(
            model_name='item',
            name='response_type',
            field=models.CharField(choices=[('Text', 'Text Response'), ('Number', 'Numeric Response'), ('Likert', 'Likert Scale'), ('Range', 'Range Response'), ('Media', 'Media Response')], db_index=True, help_text='The type of response for the item', max_length=255),
        ),
    ]
