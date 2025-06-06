# Generated by Django 5.2.1 on 2025-06-05 15:00

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('patientapp', '0007_alter_patient_age_alter_patient_gender_and_more'),
        ('promapp', '0012_alter_itemtranslation_media'),
    ]

    operations = [
        migrations.AlterField(
            model_name='patientquestionnaire',
            name='display_questionnaire',
            field=models.BooleanField(db_index=True, default=False, help_text='If True, the questionnaire is currently will be displayed for the patient'),
        ),
        migrations.AddIndex(
            model_name='item',
            index=models.Index(fields=['construct_scale', 'response_type'], name='item_construct_resptype_idx'),
        ),
        migrations.AddIndex(
            model_name='item',
            index=models.Index(fields=['construct_scale', 'item_number'], name='item_construct_number_idx'),
        ),
        migrations.AddIndex(
            model_name='patientquestionnaire',
            index=models.Index(fields=['patient', 'display_questionnaire'], name='pq_patient_display_idx'),
        ),
        migrations.AddIndex(
            model_name='patientquestionnaire',
            index=models.Index(fields=['patient', 'questionnaire'], name='pq_patient_quest_idx'),
        ),
        migrations.AddIndex(
            model_name='questionnaireconstructscore',
            index=models.Index(fields=['questionnaire_submission', 'construct'], name='qcs_submission_construct_idx'),
        ),
        migrations.AddIndex(
            model_name='questionnaireconstructscore',
            index=models.Index(fields=['construct', 'questionnaire_submission'], name='qcs_construct_submission_idx'),
        ),
        migrations.AddIndex(
            model_name='questionnaireitemresponse',
            index=models.Index(fields=['questionnaire_submission', 'questionnaire_item'], name='qir_submission_item_idx'),
        ),
        migrations.AddIndex(
            model_name='questionnaireitemresponse',
            index=models.Index(fields=['questionnaire_item', 'questionnaire_submission'], name='qir_item_submission_idx'),
        ),
        migrations.AddIndex(
            model_name='questionnaireitemresponse',
            index=models.Index(fields=['response_date'], name='qir_response_date_idx'),
        ),
        migrations.AddIndex(
            model_name='questionnairesubmission',
            index=models.Index(fields=['patient', 'submission_date'], name='qsub_patient_date_idx'),
        ),
        migrations.AddIndex(
            model_name='questionnairesubmission',
            index=models.Index(fields=['patient_questionnaire', 'submission_date'], name='qsub_pq_date_idx'),
        ),
        migrations.AddIndex(
            model_name='questionnairesubmission',
            index=models.Index(fields=['submission_date'], name='qsub_date_idx'),
        ),
    ]
