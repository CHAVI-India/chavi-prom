from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.views.generic import ListView, CreateView, UpdateView, DetailView, DeleteView, TemplateView, View
from django.urls import reverse_lazy, reverse
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin, PermissionRequiredMixin
from django.contrib import messages
from django.db import transaction
from django.utils.translation import gettext as _
from django.utils import timezone
from django.conf import settings
from django.utils import translation
from django.utils.html import escape
from django.utils.http import url_has_allowed_host_and_scheme
from .models import Questionnaire, Item, QuestionnaireItem, LikertScale, RangeScale, ConstructScale, ResponseTypeChoices, LikertScaleResponseOption, PatientQuestionnaire, QuestionnaireItemResponse, Patient, QuestionnaireItemRule, QuestionnaireItemRuleGroup, QuestionnaireSubmission, QuestionnaireConstructScore, CompositeConstructScaleScoring
from .forms import (
    QuestionnaireForm, ItemForm, QuestionnaireItemForm, 
    LikertScaleForm, LikertScaleResponseOptionFormSet,
    ItemSelectionForm, ConstructScaleForm,
    LikertScaleResponseOptionForm, RangeScaleForm,
    QuestionnaireResponseForm, StaffQuestionnaireResponseForm, QuestionnaireItemRuleForm, QuestionnaireItemRuleGroupForm,
    ItemTranslationForm, QuestionnaireTranslationForm, LikertScaleResponseOptionTranslationForm, RangeScaleTranslationForm,
    TranslationSearchForm, ConstructEquationForm, CompositeConstructScaleScoringForm
)
from django.utils.translation import get_language
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q
import json
import logging
import csv
from datetime import datetime
from django.utils.timesince import timeuntil
from django.conf import settings
from django.utils import translation
import uuid
from patientapp.models import Patient

# Create your views here.

class QuestionnaireListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    '''
    Questionnaire List View for displaying list of avaialble questionnaires.
    '''
    model = Questionnaire
    template_name = 'promapp/questionnaire_list.html'
    context_object_name = 'questionnaires'
    ordering = ['-created_date']
    permission_required = 'promapp.view_questionnaire'
    paginate_by = 10  # Show 10 questionnaires per page

    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(translations__name__icontains=search)
        
        # Apply has items filter if provided
        has_items = self.request.GET.get('has_items')
        if has_items and has_items != 'all':
            if has_items == 'yes':
                queryset = queryset.filter(questionnaireitem__isnull=False)
            elif has_items == 'no':
                queryset = queryset.filter(questionnaireitem__isnull=True)
        
        # Apply answer interval filter if provided
        answer_interval = self.request.GET.get('answer_interval')
        if answer_interval and answer_interval != 'all':
            if answer_interval == 'none':
                queryset = queryset.filter(questionnaire_answer_interval=0)
            elif answer_interval == 'has_interval':
                queryset = queryset.filter(questionnaire_answer_interval__gt=0)
            
        return queryset.distinct('id').order_by('id', 'translations__name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # Create filters for the search component
        context['questionnaire_filters'] = [
            {
                'type': 'select',
                'name': 'has_items',
                'label': 'Has items',
                'selected': self.request.GET.get('has_items', 'all'),
                'options': [
                    {'value': 'yes', 'label': 'Has items'},
                    {'value': 'no', 'label': 'No items'}
                ],
                'trigger': 'hx-trigger="change"'
            },
            {
                'type': 'select',
                'name': 'answer_interval',
                'label': 'Answer interval',
                'selected': self.request.GET.get('answer_interval', 'all'),
                'options': [
                    {'value': 'none', 'label': 'No interval'},
                    {'value': 'has_interval', 'label': 'Has interval'}
                ],
                'trigger': 'hx-trigger="change"'
            }
        ]
        
        # Add translation status data for each questionnaire
        questionnaires_with_translation_status = []
        for questionnaire in context['questionnaires']:
            # Get all existing translations for this questionnaire
            existing_translations = set(
                questionnaire.translations.values_list('language_code', flat=True)
            )
            
            translation_status = []
            for lang_code, lang_name in settings.LANGUAGES:
                has_translation = lang_code in existing_translations
                # Check if translation has content (not just empty strings)
                if has_translation:
                    try:
                        translation = questionnaire.translations.get(language_code=lang_code)
                        has_content = bool(
                            (translation.name and translation.name.strip()) or 
                            (translation.description and translation.description.strip())
                        )
                    except questionnaire.translations.model.DoesNotExist:
                        has_content = False
                else:
                    has_content = False
                    
                translation_status.append({
                    'language_code': lang_code,
                    'language_name': lang_name,
                    'has_translation': has_translation and has_content,
                })
            
            questionnaires_with_translation_status.append({
                'questionnaire': questionnaire,
                'translation_status': translation_status,
            })
        
        context['questionnaires_with_translation_status'] = questionnaires_with_translation_status
        return context

    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # For HTMX requests, let Django handle pagination normally
            # but just return the partial template
            response = super().get(request, *args, **kwargs)
            
            # If the superclass call resulted in a successful response
            if hasattr(response, 'context_data'):
                context = response.context_data
            else:
                # Fallback to getting context manually
                context = self.get_context_data()
            
            # Render only the table part for HTMX
            html = render_to_string('promapp/partials/questionnaire_list_table.html', context, request=request)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)


class QuestionnaireDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    '''
    Questionnaire Detail View for displaying details of a specific questionnaire.
    '''
    model = Questionnaire
    template_name = 'promapp/questionnaire_detail.html'
    context_object_name = 'questionnaire'
    permission_required = 'promapp.view_questionnaire'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Get all items associated with this questionnaire
        questionnaire = self.get_object()
        current_language = get_language()
        items = Item.objects.language(current_language).filter(
            id__in=QuestionnaireItem.objects.filter(
                questionnaire=questionnaire
            ).values_list('item', flat=True).distinct()
        )
        context['items'] = items
        context['available_languages'] = settings.LANGUAGES
        return context


class QuestionnaireCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    '''
    Questionnaire Create View for creating a new questionnaire.
    '''
    model = Questionnaire
    form_class = QuestionnaireForm
    template_name = 'promapp/questionnaire_create.html'
    success_url = reverse_lazy('questionnaire_list')
    permission_required = 'promapp.add_questionnaire'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['item_selection_form'] = ItemSelectionForm()
        current_language = get_language()
        
        # Get items with translations, but don't rely on distinct() due to django-parler issues
        all_items = Item.objects.language(current_language).prefetch_related(
            'construct_scale',
            'likert_response', 
            'range_response'
        ).order_by('translations__name')
        
        # Manual deduplication by ID to ensure no duplicates
        seen_ids = set()
        unique_items = []
        for item in all_items:
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                unique_items.append(item)
        
        # Create a list of items with their construct scales for display
        # Each item appears only once with all its construct scales listed
        items_with_constructs = []
        for item in unique_items:
            construct_scales = list(item.construct_scale.all())
            items_with_constructs.append({
                'item': item,
                'constructs': construct_scales,
                'construct_names': ', '.join([c.name for c in construct_scales]) if construct_scales else 'No Construct Scale',
                'instrument_names': ', '.join(set([c.instrument_name for c in construct_scales if c.instrument_name])) if construct_scales else '',
                'construct_ids': [str(c.id) for c in construct_scales] if construct_scales else ['none']
            })
        
        context['items_with_constructs'] = items_with_constructs
        context['grouped_items'] = None  # Deprecated, keeping for backward compatibility
        context['available_items'] = unique_items  # Keep for backward compatibility
        context['construct_scales'] = ConstructScale.objects.all().order_by('name')
        
        # Get unique instrument names for filtering
        instrument_names = ConstructScale.objects.exclude(
            instrument_name__isnull=True
        ).exclude(
            instrument_name__exact=''
        ).values_list('instrument_name', flat=True).distinct().order_by('instrument_name')
        context['instrument_names'] = instrument_names
        
        context['questionnaire_items'] = []  # Always empty for create view
        
        # Add rules and rule groups context
        if self.object:
            questionnaire_items = QuestionnaireItem.objects.filter(questionnaire=self.object)
            context['rules'] = QuestionnaireItemRule.objects.filter(
                questionnaire_item__in=questionnaire_items
            ).order_by('rule_order')
            context['rule_groups'] = QuestionnaireItemRuleGroup.objects.filter(
                questionnaire_item__in=questionnaire_items
            ).order_by('group_order')
        
        return context

    @transaction.atomic
    def form_valid(self, form):
        # Save the questionnaire
        questionnaire = form.save()
        
        # Process selected items
        item_selection_form = ItemSelectionForm(self.request.POST)
        if item_selection_form.is_valid():
            selected_items = item_selection_form.cleaned_data.get('items', [])
            
            # Create QuestionnaireItem objects for each selected item
            for item in selected_items:
                # Get the question number for this item
                question_number = self.request.POST.get(f'question_number_{item.id}')
                
                QuestionnaireItem.objects.create(
                    questionnaire=questionnaire,
                    item=item,
                    question_number=question_number if question_number else None
                )
            
            # Use getattr with default value instead of safe_translation_getter
            questionnaire_name = getattr(questionnaire, 'name', 'New Questionnaire')
            messages.success(self.request, f"Questionnaire '{questionnaire_name}' created successfully with {len(selected_items)} items.")
        else:
            messages.warning(self.request, "Questionnaire created, but there was an issue with item selection.")
            
        return redirect(self.success_url)


class QuestionnaireUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    '''
    Questionnaire Update View for updating an existing questionnaire.
    '''
    model = Questionnaire
    form_class = QuestionnaireForm
    template_name = 'promapp/questionnaire_update.html'
    success_url = reverse_lazy('questionnaire_list')
    permission_required = 'promapp.change_questionnaire'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questionnaire = self.get_object()
        from django.db.models import Prefetch
        # Prefetch rules and rule groups for all questionnaire items
        raw_items = QuestionnaireItem.objects.filter(
            questionnaire=questionnaire
        ).order_by('question_number').prefetch_related(
            Prefetch('visibility_rules', queryset=QuestionnaireItemRule.objects.order_by('rule_order')),
            Prefetch('rule_groups', queryset=QuestionnaireItemRuleGroup.objects.order_by('group_order').prefetch_related('rules'))
        )
        questionnaire_items_structured = []
        for item in raw_items:
            rules = list(item.visibility_rules.all())
            groups = list(item.rule_groups.all())
            grouped_rule_ids = set()
            for group in groups:
                grouped_rule_ids.update(r.id for r in group.rules.all())
            ungrouped_rules = [r for r in rules if r.id not in grouped_rule_ids]
            questionnaire_items_structured.append({
                'item': item,
                'rules': rules,
                'rule_groups': groups,
                'ungrouped_rules': ungrouped_rules,
            })
        context['questionnaire_items_structured'] = questionnaire_items_structured
        item_to_question_number = {str(qi.item.id): qi.question_number for qi in raw_items}
        current_language = get_language()
        
        # Get all items with translations, but don't rely on distinct() due to django-parler issues
        all_items = Item.objects.language(current_language).prefetch_related(
            'construct_scale',
        ).select_related(
            'likert_response',
            'range_response'
        ).order_by('translations__name')
        
        # Manual deduplication by ID to ensure no duplicates
        seen_ids = set()
        unique_items = []
        
        for item in all_items:
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                unique_items.append(item)
        
        current_items = Item.objects.filter(
            id__in=raw_items.values_list('item', flat=True)
        )
        
        # Create a list of items with their question numbers
        items_with_numbers = []
        for item in unique_items:
            item.question_number = item_to_question_number.get(str(item.id))
            items_with_numbers.append(item)
        
        # Sort items by question number (None values go to the end)
        items_with_numbers.sort(key=lambda x: (x.question_number is None, x.question_number))
        
        # Create a list of items with their construct scales for display
        # Each item appears only once with all its construct scales listed
        items_with_constructs = []
        for item in items_with_numbers:
            construct_scales = list(item.construct_scale.all())
            items_with_constructs.append({
                'item': item,
                'constructs': construct_scales,
                'construct_names': ', '.join([c.name for c in construct_scales]) if construct_scales else 'No Construct Scale',
                'instrument_names': ', '.join(set([c.instrument_name for c in construct_scales if c.instrument_name])) if construct_scales else '',
                'construct_ids': [str(c.id) for c in construct_scales] if construct_scales else ['none']
            })
        
        context['items_with_constructs'] = items_with_constructs
        context['grouped_items'] = None  # Deprecated, keeping for backward compatibility
        context['item_selection_form'] = ItemSelectionForm(initial={'items': current_items})
        context['available_items'] = items_with_numbers  # Keep for backward compatibility
        context['construct_scales'] = ConstructScale.objects.all().order_by('name')
        
        # Get unique instrument names for filtering
        instrument_names = ConstructScale.objects.exclude(
            instrument_name__isnull=True
        ).exclude(
            instrument_name__exact=''
        ).values_list('instrument_name', flat=True).distinct().order_by('instrument_name')
        context['instrument_names'] = instrument_names
        
        context['rule_groups'] = QuestionnaireItemRuleGroup.objects.filter(
            questionnaire_item__in=raw_items
        ).order_by('group_order')
        return context

    @transaction.atomic
    def form_valid(self, form):
        try:
            questionnaire = form.save()
            
            # Process selected items
            item_selection_form = ItemSelectionForm(self.request.POST)
            if item_selection_form.is_valid():
                selected_items = item_selection_form.cleaned_data.get('items', [])
                
                # Get existing questionnaire items
                existing_items = QuestionnaireItem.objects.filter(
                    questionnaire=questionnaire
                ).select_related('item')
                
                # Create a mapping of item IDs to their existing questionnaire items
                existing_item_map = {str(qi.item.id): qi for qi in existing_items}
                
                # Track which items we've processed
                processed_item_ids = set()
                
                # Process selected items
                for item in selected_items:
                    question_number = self.request.POST.get(f'question_number_{item.id}')
                    if question_number:
                        try:
                            question_number = int(question_number)
                        except (ValueError, TypeError):
                            question_number = None
                    
                    if str(item.id) not in existing_item_map:
                        # Create new questionnaire item
                        QuestionnaireItem.objects.create(
                            questionnaire=questionnaire,
                            item=item,
                            question_number=question_number
                        )
                    else:
                        # Update existing questionnaire item
                        qi = existing_item_map[str(item.id)]
                        qi.question_number = question_number
                        qi.save()
                    
                    processed_item_ids.add(str(item.id))
                
                # Remove questionnaire items that are no longer selected
                items_to_remove = existing_items.exclude(item__id__in=processed_item_ids)
                if items_to_remove.exists():
                    items_to_remove.delete()
                
                questionnaire_name = getattr(questionnaire, 'name', 'Questionnaire')
                messages.success(self.request, f"Questionnaire '{questionnaire_name}' updated successfully with {len(selected_items)} items.")
            else:
                messages.warning(self.request, "Questionnaire updated, but there was an issue with item selection.")
            
            # Stay on the same update page after saving
            return redirect('questionnaire_update', pk=questionnaire.id)
            
        except ValidationError as e:
            messages.error(self.request, str(e))
            return self.form_invalid(form)
        except Exception as e:
            # Log the detailed error for debugging but show generic message to user
            logger = logging.getLogger("promapp.questionnaire_management")
            logger.error(f"Unexpected error updating questionnaire {questionnaire.id if 'questionnaire' in locals() else 'unknown'}: {str(e)}")
            messages.error(self.request, "An error occurred while updating the questionnaire. Please try again or contact support if the problem persists.")
            return self.form_invalid(form)


class ItemListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    '''
    Item List View for displaying list of available items.
    '''
    model = Item
    template_name = 'promapp/item_list.html'
    context_object_name = 'items'
    permission_required = 'promapp.view_item'
    paginate_by = 10  # Show 5 items per page for testing
    
    def get_queryset(self):
        current_language = get_language()
        # Start with base queryset and prefetch related fields (ManyToMany requires prefetch)
        queryset = Item.objects.language(current_language).prefetch_related(
            'construct_scale',
        ).select_related(
            'likert_response',
            'range_response'
        )
        
        # Apply filters based on query parameters
        construct_scale = self.request.GET.get('construct_scale')
        response_type = self.request.GET.get('response_type')
        instrument_name = self.request.GET.get('instrument_name')
        search = self.request.GET.get('search')
        
        if construct_scale and construct_scale != 'all':
            # Filter by construct_scale for ManyToMany relationship
            queryset = queryset.filter(construct_scale__id=construct_scale)
        
        if response_type and response_type != 'all':
            queryset = queryset.filter(response_type=response_type)
        
        if instrument_name and instrument_name != 'all':
            # Filter by instrument name through the construct_scale relationship
            queryset = queryset.filter(construct_scale__instrument_name=instrument_name)
            
        if search:
            queryset = queryset.filter(translations__name__icontains=search)
            
        # Use distinct() with id to prevent duplicates while keeping all fields
        return queryset.distinct('id').order_by('id', 'translations__name')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Get all construct scales for the filter dropdown
        context['all_construct_scales'] = ConstructScale.objects.all().order_by('name')
        # Get all response types for the filter dropdown
        context['response_types'] = [
            {'value': choice[0], 'display': choice[1]} 
            for choice in ResponseTypeChoices.choices
        ]
        # Get all unique instrument names for the filter dropdown
        instrument_names = ConstructScale.objects.exclude(
            instrument_name__isnull=True
        ).exclude(
            instrument_name__exact=''
        ).values_list('instrument_name', flat=True).distinct().order_by('instrument_name')
        context['instrument_names'] = list(instrument_names)
        
        # Keep the selected filters in the context
        context['selected_construct_scale'] = self.request.GET.get('construct_scale', 'all')
        context['selected_response_type'] = self.request.GET.get('response_type', 'all')
        context['selected_instrument_name'] = self.request.GET.get('instrument_name', 'all')
        context['search_query'] = self.request.GET.get('search', '')
        
        # Add available languages to context
        context['available_languages'] = settings.LANGUAGES
        
        # Add current language to context for translation links
        context['current_language'] = get_language()
        
        # Flag to determine if we're responding to an HTMX request
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        return context
    
    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # For HTMX requests, let Django handle pagination normally
            # but just return the partial template
            response = super().get(request, *args, **kwargs)
            
            # If the superclass call resulted in a successful response
            if hasattr(response, 'context_data'):
                context = response.context_data
            else:
                # Fallback to getting context manually
                context = self.get_context_data()
            
            # Render only the table part for HTMX
            html = render_to_string('promapp/partials/item_list_table.html', context, request=request)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)


class ItemCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    '''
    View to create items
    '''
    model = Item
    form_class = ItemForm
    template_name = 'promapp/item_create.html'
    success_url = reverse_lazy('item_list')
    permission_required = 'promapp.add_item'

    def get_initial(self):
        initial = super().get_initial()
        # Pre-populate construct_scale if passed as URL parameter
        # For ManyToMany field, this should be a list
        construct_scale_id = self.request.GET.get('construct_scale')
        if construct_scale_id:
            try:
                # Validate that the construct scale exists
                ConstructScale.objects.get(id=construct_scale_id)
                # For ManyToMany, initial value should be a list
                initial['construct_scale'] = [construct_scale_id]
            except ConstructScale.DoesNotExist:
                pass  # Invalid ID, ignore
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['likert_scales'] = LikertScale.objects.all()
        context['range_scales'] = RangeScale.objects.all()
        return context

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ValidationError as e:
            # Convert ValidationError to form error
            if hasattr(e, 'message_dict'):
                # Multiple field errors
                for field, errors in e.message_dict.items():
                    for error in errors:
                        form.add_error(field, error)
            elif hasattr(e, 'messages'):
                # Non-field errors (general errors)
                for error in e.messages:
                    form.add_error(None, error)
            else:
                # Log detailed error but show generic message
                logger = logging.getLogger("promapp.item_management")
                logger.error(f"Unexpected error creating item: {str(e)}")
                form.add_error(None, "An unexpected error occurred. Please try again.")
            
            messages.error(self.request, "There was an error creating the item. Please check the form for details.")
            return self.form_invalid(form)


class ItemUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    '''
    View to update items
    '''
    model = Item
    form_class = ItemForm
    template_name = 'promapp/item_update.html'
    success_url = reverse_lazy('item_list')
    permission_required = 'promapp.change_item'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['likert_scales'] = LikertScale.objects.all()
        context['range_scales'] = RangeScale.objects.all()
        
        # Add the selected scales to the context
        if self.object:
            context['selected_likert_scale'] = str(self.object.likert_response.id) if self.object.likert_response else None
            context['selected_range_scale'] = str(self.object.range_response.id) if self.object.range_response else None
        
        return context

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ValidationError as e:
            # Convert ValidationError to form error
            if hasattr(e, 'message_dict'):
                # Multiple field errors
                for field, errors in e.message_dict.items():
                    for error in errors:
                        form.add_error(field, error)
            elif hasattr(e, 'messages'):
                # Non-field errors (general errors)
                for error in e.messages:
                    form.add_error(None, error)
            else:
                # Log detailed error but show generic message
                logger = logging.getLogger("promapp.item_management")
                logger.error(f"Unexpected error updating item: {str(e)}")
                form.add_error(None, "An unexpected error occurred. Please try again.")
            
            messages.error(self.request, "There was an error updating the item. Please check the form for details.")
            return self.form_invalid(form)


def get_response_fields(request):
    '''
    Get the response fields based on the selected response type
    '''
    response_type = request.GET.get('response_type')
    selected_likert_scale = request.GET.get('likert_response')
    selected_range_scale = request.GET.get('range_response')
    
    # Get the item instance if we're editing
    item_id = request.GET.get('item_id')
    if item_id:
        try:
            item = Item.objects.get(id=item_id)
            if not selected_likert_scale and item.likert_response:
                selected_likert_scale = str(item.likert_response.id)
            if not selected_range_scale and item.range_response:
                selected_range_scale = str(item.range_response.id)
        except Item.DoesNotExist:
            pass
    
    html = render_to_string('promapp/response_fields.html', {
        'response_type': response_type,
        'likert_scales': LikertScale.objects.all(),
        'range_scales': RangeScale.objects.all(),
        'selected_likert_scale': selected_likert_scale,
        'selected_range_scale': selected_range_scale
    })
    return HttpResponse(html)

def add_item_form(request):
    '''
    View to add a new item
    '''
    item_form = ItemForm()
    html = render_to_string('questionnaire/item_form.html', {
        'item_form': item_form,
        'forloop': {'counter': request.GET.get('counter', 1)}
    })
    return HttpResponse(html)

def create_likert_scale(request):
    '''
    View to create a new likert scale
    '''
    # Check if we're editing an existing Likert scale
    edit_id = request.GET.get('edit')
    instance = None
    
    if edit_id:
        instance = get_object_or_404(LikertScale, pk=edit_id)
        print(f"Editing likert scale: {instance.likert_scale_name} (ID: {instance.id})")
    
    if request.method == 'POST':
        # Collect and debug POST data
        print("POST data:", request.POST)
        
        # Get the standard formset prefix
        prefix = 'likertscaleresponseoption_set'
        print(f"Standard formset prefix: {prefix}")
        print(f"TOTAL_FORMS: {request.POST.get(f'{prefix}-TOTAL_FORMS')}")
        print(f"INITIAL_FORMS: {request.POST.get(f'{prefix}-INITIAL_FORMS')}")
        
        # Process the form for the likert scale itself
        form = LikertScaleForm(request.POST, instance=instance)
        
        # Process the standard formset
        if instance:
            formset = LikertScaleResponseOptionFormSet(request.POST, request.FILES, instance=instance)
        else:
            formset = LikertScaleResponseOptionFormSet(request.POST, request.FILES)
        
        # Debug formset
        print(f"Formset has {len(formset.forms)} forms")
        for i, form_instance in enumerate(formset.forms):
            print(f"Formset form {i} data:", {
                'option_order': form_instance['option_order'].value(),
                'option_value': form_instance['option_value'].value(),
                'option_text': form_instance['option_text'].value(),
            })
        
        # Check form validity first
        valid_form = form.is_valid()
        valid_formset = formset.is_valid()
        
        # Also collect any dynamically added forms (with 'form-' prefix)
        dynamic_forms = []
        
        # We need to process the raw request data to get all form instances
        # This is because request.POST.getlist() doesn't handle nested lists properly
        
        # First, identify all the unique form indices in the dynamic forms
        form_indices = set()
        for key in request.POST:
            if key.startswith('form-') and '-option_order' in key:
                parts = key.split('-')
                if len(parts) == 3 and parts[1].isdigit():  # form-X-field format
                    form_indices.add(parts[1])
        
        print(f"Found {len(form_indices)} unique form indices: {', '.join(form_indices)}")
        
        # For each form index, extract all fields
        for form_index in form_indices:
            option_order = request.POST.get(f'form-{form_index}-option_order', '')
            option_value = request.POST.get(f'form-{form_index}-option_value', '')
            option_text = request.POST.get(f'form-{form_index}-option_text', '')
            option_emoji = request.POST.get(f'form-{form_index}-option_emoji', '')
            likert_scale_id = request.POST.get(f'form-{form_index}-likert_scale', '')
            
            # Only add non-empty forms
            if option_order.strip() or option_text.strip():
                dynamic_forms.append({
                    'index': form_index,
                    'option_order': option_order.strip(),
                    'option_value': option_value.strip(),
                    'option_text': option_text.strip(),
                    'option_emoji': option_emoji.strip(),
                    'likert_scale_id': likert_scale_id.strip()
                })
        
        print(f"Found {len(dynamic_forms)} dynamically added form entries")
        for i, dform in enumerate(dynamic_forms):
            print(f"Dynamic form {i+1} data:", dform)
        
        if valid_form and valid_formset:
            with transaction.atomic():
                # First save the likert scale
                likert_scale = form.save()
                print(f"Saved likert scale: {likert_scale.likert_scale_name} (ID: {likert_scale.id})")
                
                # Save the standard formset (skip empty forms)
                formset.instance = likert_scale
                saved_options = []
                for form_instance in formset.forms:
                    # Check if this form has data and isn't marked for deletion
                    if form_instance.is_valid():
                        # Only process forms with actual data
                        option_order = form_instance.cleaned_data.get('option_order')
                        option_text = form_instance.cleaned_data.get('option_text', '')
                        option_value = form_instance.cleaned_data.get('option_value')
                        delete_flag = form_instance.cleaned_data.get('DELETE', False)
                        
                        # Debug
                        print(f"Processing form with data: order={option_order}, value={option_value}, text={option_text}, delete={delete_flag}")
                        
                        if not delete_flag and (option_order is not None or option_text):
                            try:
                                # Handle existing or new instances
                                if form_instance.instance.pk:
                                    option = form_instance.instance
                                    if option_order is not None:
                                        option.option_order = option_order
                                    if option_value is not None:
                                        option.option_value = option_value
                                    if option_text:
                                        option.option_text = option_text
                                    option.likert_scale = likert_scale
                                else:
                                    # Create new option
                                    option = LikertScaleResponseOption()
                                    option.likert_scale = likert_scale
                                    option.option_order = option_order if option_order is not None else 0
                                    option.option_value = option_value if option_value is not None else 0
                                    option.option_text = option_text
                                
                                try:
                                    # Save and track
                                    option.save()
                                    saved_options.append(option)
                                    print(f"Saved option: {option.option_text} (order: {option.option_order}, value: {option.option_value})")
                                except Exception as e:
                                    if 'unique constraint' in str(e).lower():
                                        error_msg = f"Cannot save option: A response option with order {option.option_order} and value {option.option_value} already exists in this scale."
                                        print(error_msg)
                                        messages.error(request, error_msg)
                                    else:
                                        print(f"Error saving option: {e}")
                                        logger = logging.getLogger("promapp.likert_scale_management")
                                        logger.error(f"Unexpected error saving likert option: {str(e)}")
                                        messages.error(request, "Error saving option. Please check your input and try again.")
                            except Exception as e:
                                print(f"Error processing option: {e}")
                                logger = logging.getLogger("promapp.likert_scale_management")
                                logger.error(f"Unexpected error processing likert option: {str(e)}")
                                messages.error(request, "Error processing option. Please check your input and try again.")
                        elif delete_flag and form_instance.instance.pk:
                            # Delete if marked and exists
                            form_instance.instance.delete()
                            print(f"Deleted option with ID: {form_instance.instance.pk}")
                    else:
                        print(f"Form validation failed: {form_instance.errors}")
                
                print(f"Saved {len(saved_options)} options from formset")
                
                # Process and save dynamically added forms manually
                for dform in dynamic_forms:
                    try:
                        # Create a new option
                        option = LikertScaleResponseOption()
                        option.likert_scale = likert_scale
                        
                        # Handle empty values
                        try:
                            option.option_order = int(dform['option_order']) if dform['option_order'].strip() else 0
                        except (ValueError, TypeError):
                            option.option_order = 0
                        
                        try:
                            # Explicitly handle '0' or '0.00' values
                            option_value = dform['option_value'].strip()
                            if option_value == '' or option_value is None:
                                option.option_value = 0
                            else:
                                option.option_value = float(option_value)
                        except (ValueError, TypeError):
                            option.option_value = 0
                        
                        option.option_text = dform['option_text']
                        option.option_emoji = dform['option_emoji'] if dform['option_emoji'] else None
                        
                        # Check for duplicates before saving
                        try:
                            option.save()
                            print(f"Saved dynamic option: {option.option_text} (order: {option.option_order}, value: {option.option_value})")
                        except Exception as e:
                            if 'unique constraint' in str(e).lower():
                                error_msg = f"Cannot save dynamic option: A response option with order {option.option_order} and value {option.option_value} already exists in this scale."
                                print(error_msg)
                                messages.error(request, error_msg)
                            else:
                                print(f"Error saving dynamic option: {e}")
                                logger = logging.getLogger("promapp.likert_scale_management")
                                logger.error(f"Unexpected error saving dynamic likert option: {str(e)}")
                                messages.error(request, "Error saving dynamic option. Please check your input and try again.")
                    except Exception as e:
                        print(f"Error processing dynamic option: {e}")
                        logger = logging.getLogger("promapp.likert_scale_management")
                        logger.error(f"Unexpected error processing dynamic likert option: {str(e)}")
                        messages.error(request, "Error processing dynamic option. Please check your input and try again.")
                
                if instance:
                    messages.success(request, "Likert scale updated successfully.")
                else:
                    messages.success(request, "Likert scale created successfully.")
                
                # Redirect to the likert scale list if available, otherwise to item create
                if request.user.has_perm('promapp.view_likertscale'):
                    return redirect('likert_scale_list')
                else:
                    return redirect('item_create')
        else:
            if not valid_form:
                print("Form errors:", form.errors)
                messages.error(request, "Please check the scale details for errors.")
            if not valid_formset:
                print("Formset errors:", formset.errors)
                print("Formset non-form errors:", formset.non_form_errors())
                for i, form_errors in enumerate(formset.errors):
                    if form_errors:
                        print(f"Form {i} errors:", form_errors)
                messages.error(request, "Please check the response options for errors.")
    else:
        # Initialize form and formset with instance data if editing
        form = LikertScaleForm(instance=instance)
        
        if instance:
            formset = LikertScaleResponseOptionFormSet(instance=instance)
        else:
            formset = LikertScaleResponseOptionFormSet()
    
    context = {
        'form': form,
        'formset': formset,
        'is_edit': bool(instance),
        'scale': instance,
        'dynamic_row_count': request.POST.get('dynamic_row_count', 0) if request.method == 'POST' else 0
    }
    
    return render(request, 'promapp/likert_scale_form.html', context)


def create_construct_scale(request):
    """
    Function-based view for creating construct scales
    """
    if request.method == 'POST':
        form = ConstructScaleForm(request.POST)
        if form.is_valid():
            construct_scale = form.save()
            messages.success(request, "Construct scale created successfully.")
            # Get the referrer URL, defaulting to item_create if not available
            referrer = request.META.get('HTTP_REFERER')
            if referrer and 'item_create' in referrer:
                return redirect('item_create')
            elif referrer and 'item_update' in referrer:
                return redirect('item_update', pk=request.GET.get('item_id'))
            else:
                return redirect('construct_scale_list')
    else:
        form = ConstructScaleForm()
    
    return render(request, 'promapp/construct_scale_form.html', {
        'form': form,
        'referrer': request.META.get('HTTP_REFERER', '')
    })

def add_likert_option(request):
    """Add a new empty row to the Likert scale formset."""
    # Extract all parameters from the request for debugging
    params = {key: request.GET.get(key) for key in request.GET}
    print(f"Add option request parameters: {params}")
    
    # Get the form index (default to a safe value if missing)
    form_index = int(request.GET.get('form_index', 0))
    
    # Get the likert scale ID if it's being edited
    scale_id = request.GET.get('scale_id', None)
    scale = None
    
    # Get suggested order and value directly from request
    # Explicitly handle the conversion to avoid type errors
    try:
        suggested_order = int(request.GET.get('next_order', 2))
    except (ValueError, TypeError):
        suggested_order = 2
        
    try:
        suggested_value = float(request.GET.get('next_value', 1))
    except (ValueError, TypeError):
        suggested_value = 1.0
    
    # Handle edge case of first row
    if suggested_order == 1:
        suggested_value = 0.0
    
    if scale_id:
        try:
            scale = LikertScale.objects.get(pk=scale_id)
            print(f"Found likert scale: {scale.likert_scale_name} (ID: {scale.id})")
            
            # If we have a scale but no suggested values in request, determine them
            if 'next_order' not in request.GET:
                max_order = LikertScaleResponseOption.objects.filter(
                    likert_scale=scale
                ).aggregate(models.Max('option_order'))['option_order__max'] or 0
                suggested_order = max_order + 1
                
            if 'next_value' not in request.GET:
                max_value = LikertScaleResponseOption.objects.filter(
                    likert_scale=scale
                ).aggregate(models.Max('option_value'))['option_value__max'] or 0
                suggested_value = float(max_value) + 1
        except LikertScale.DoesNotExist:
            print(f"LikertScale with ID {scale_id} not found")
    
    # Render a new empty form row
    context = {
        'form_index': form_index,
        'scale': scale,
        'suggested_order': suggested_order,
        'suggested_value': suggested_value,
    }
    
    # Log debug info
    print(f"Adding option row with index {form_index}, scale_id: {scale_id}, suggested_order: {suggested_order}, suggested_value: {suggested_value}")
    
    return render(request, 'promapp/likert_option_row.html', context)

def remove_likert_option(request):
    """Remove a row from the Likert scale formset."""
    # Return an empty response to remove the row
    return HttpResponse('')

class LikertScaleListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    '''
    View to list all likert scales
    '''
    model = LikertScale
    template_name = 'promapp/likert_scale_list.html'
    context_object_name = 'likert_scales'
    permission_required = 'promapp.view_likertscale'
    paginate_by = 10  # Show 10 likert scales per page
    
    def get_queryset(self):
        queryset = LikertScale.objects.all()
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(likert_scale_name__icontains=search)
        
        # Apply option count filter if provided
        option_count = self.request.GET.get('option_count')
        if option_count and option_count != 'all':
            # Annotate with option count first
            queryset = queryset.annotate(
                num_options=models.Count('likertscaleresponseoption', distinct=True)
            )
            
            if option_count == 'few':
                # 2-3 options
                queryset = queryset.filter(num_options__gte=2, num_options__lte=3)
            elif option_count == 'medium':
                # 4-5 options
                queryset = queryset.filter(num_options__gte=4, num_options__lte=5)
            elif option_count == 'many':
                # 6+ options
                queryset = queryset.filter(num_options__gte=6)
            elif option_count == 'none':
                # No options
                queryset = queryset.filter(num_options=0)
        
        # Apply creation date filter if provided
        created_filter = self.request.GET.get('created_filter')
        if created_filter and created_filter != 'all':
            from datetime import datetime, timedelta
            from django.utils import timezone
            
            now = timezone.now()
            if created_filter == 'today':
                queryset = queryset.filter(created_date__date=now.date())
            elif created_filter == 'week':
                week_ago = now - timedelta(days=7)
                queryset = queryset.filter(created_date__gte=week_ago)
            elif created_filter == 'month':
                month_ago = now - timedelta(days=30)
                queryset = queryset.filter(created_date__gte=month_ago)
            elif created_filter == 'older':
                month_ago = now - timedelta(days=30)
                queryset = queryset.filter(created_date__lt=month_ago)
            
        return queryset.order_by('-created_date')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Add response options for each likert scale
        likert_scales_with_options = []
        current_language = get_language()
        
        for scale in context['likert_scales']:
            options = LikertScaleResponseOption.objects.language(current_language).filter(
                likert_scale=scale
            ).order_by('option_order')
            
            # Add translation status for each option
            options_with_translation_status = []
            for option in options:
                # Get all existing translations for this option
                existing_translations = set(
                    option.translations.values_list('language_code', flat=True)
                )
                
                translation_status = []
                for lang_code, lang_name in settings.LANGUAGES:
                    has_translation = lang_code in existing_translations
                    # Check if translation has content (not just empty strings)
                    if has_translation:
                        try:
                            translation = option.translations.get(language_code=lang_code)
                            has_content = bool(translation.option_text and translation.option_text.strip())
                        except option.translations.model.DoesNotExist:
                            has_content = False
                    else:
                        has_content = False
                        
                    translation_status.append({
                        'language_code': lang_code,
                        'language_name': lang_name,
                        'has_translation': has_translation and has_content,
                        'url': reverse('likert_scale_response_option_translation', args=[option.id]) + f'?language={lang_code}'
                    })
                
                options_with_translation_status.append({
                    'option': option,
                    'translation_status': translation_status
                })
            
            # Calculate translation counts for each language
            translation_counts = {}
            for lang_code, lang_name in settings.LANGUAGES:
                count = sum(1 for option_data in options_with_translation_status 
                           for status in option_data['translation_status'] 
                           if status['language_code'] == lang_code and status['has_translation'])
                translation_counts[lang_code] = {
                    'count': count,
                    'total': options.count(),
                    'language_name': lang_name
                }
            
            likert_scales_with_options.append({
                'scale': scale,
                'options': options,
                'options_with_translation_status': options_with_translation_status,
                'option_count': options.count(),
                'translation_counts': translation_counts
            })
        
        context['likert_scales_with_options'] = likert_scales_with_options
        context['available_languages'] = settings.LANGUAGES
        context['search_query'] = self.request.GET.get('search', '')
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # Create filters for the search component
        context['likert_scale_filters'] = [
            {
                'type': 'select',
                'name': 'option_count',
                'label': 'Number of options',
                'selected': self.request.GET.get('option_count', 'all'),
                'options': [
                    {'value': 'none', 'label': 'No options'},
                    {'value': 'few', 'label': 'Few (2-3)'},
                    {'value': 'medium', 'label': 'Medium (4-5)'},
                    {'value': 'many', 'label': 'Many (6+)'}
                ]
            },
            {
                'type': 'select',
                'name': 'created_filter',
                'label': 'Created',
                'selected': self.request.GET.get('created_filter', 'all'),
                'options': [
                    {'value': 'today', 'label': 'Today'},
                    {'value': 'week', 'label': 'This week'},
                    {'value': 'month', 'label': 'This month'},
                    {'value': 'older', 'label': 'Older than 30 days'}
                ]
            }
        ]
        
        return context
    
    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # For HTMX requests, let Django handle pagination normally
            # but just return the partial template
            response = super().get(request, *args, **kwargs)
            
            # If the superclass call resulted in a successful response
            if hasattr(response, 'context_data'):
                context = response.context_data
            else:
                # Fallback to getting context manually
                context = self.get_context_data()
            
            # Render only the table part for HTMX
            html = render_to_string('promapp/partials/likert_scale_list_table.html', context, request=request)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)

def create_range_scale(request):
    # Check if we're editing an existing Range scale
    edit_id = request.GET.get('edit')
    instance = None
    
    if edit_id:
        instance = get_object_or_404(RangeScale, pk=edit_id)
        print(f"Editing range scale: {instance.range_scale_name} (ID: {instance.id})")
    
    if request.method == 'POST':
        form = RangeScaleForm(request.POST, instance=instance)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    range_scale = form.save()
                    
                    # Validate the range values
                    if range_scale.min_value >= range_scale.max_value:
                        raise ValidationError("Minimum value must be less than maximum value")
                    if range_scale.increment <= 0:
                        raise ValidationError("Increment must be greater than 0")
                    if (range_scale.max_value - range_scale.min_value) % range_scale.increment != 0:
                        raise ValidationError("Maximum value minus minimum value must be divisible by increment")
                    
                    if instance:
                        messages.success(request, "Range scale updated successfully.")
                    else:
                        messages.success(request, "Range scale created successfully.")
                    
                    # Redirect to the range scale list if available, otherwise to item create
                    if request.user.has_perm('promapp.view_rangescale'):
                        return redirect('range_scale_list')
                    else:
                        return redirect('item_create')
            except ValidationError as e:
                messages.error(request, str(e))
            except Exception as e:
                # Log detailed error but show generic message
                logger = logging.getLogger("promapp.range_scale_management")
                logger.error(f"Unexpected error saving range scale: {str(e)}")
                messages.error(request, "Error saving range scale. Please check your input and try again.")
        else:
            messages.error(request, "Please check the form for errors.")
    else:
        form = RangeScaleForm(instance=instance)
    
    context = {
        'form': form,
        'is_edit': bool(instance),
        'scale': instance
    }
    
    return render(request, 'promapp/range_scale_form.html', context)

class RangeScaleListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    '''
    View to list all range scales
    '''
    model = RangeScale
    template_name = 'promapp/range_scale_list.html'
    context_object_name = 'range_scales'
    permission_required = 'promapp.view_rangescale'
    paginate_by = 10  # Show 10 range scales per page
    
    def get_queryset(self):
        queryset = RangeScale.objects.all()
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(range_scale_name__icontains=search)
        
        # Apply range size filter if provided
        range_size = self.request.GET.get('range_size')
        if range_size and range_size != 'all':
            if range_size == 'small':
                # Range of 10 or less
                queryset = queryset.filter(
                    max_value__isnull=False,
                    min_value__isnull=False
                ).extra(where=["max_value - min_value <= 10"])
            elif range_size == 'medium':
                # Range between 11 and 100
                queryset = queryset.filter(
                    max_value__isnull=False,
                    min_value__isnull=False
                ).extra(where=["max_value - min_value > 10 AND max_value - min_value <= 100"])
            elif range_size == 'large':
                # Range greater than 100
                queryset = queryset.filter(
                    max_value__isnull=False,
                    min_value__isnull=False
                ).extra(where=["max_value - min_value > 100"])
        
        # Apply has increment filter if provided
        has_increment = self.request.GET.get('has_increment')
        if has_increment and has_increment != 'all':
            if has_increment == 'yes':
                queryset = queryset.exclude(increment__isnull=True)
            elif has_increment == 'no':
                queryset = queryset.filter(increment__isnull=True)
            
        return queryset.order_by('-created_date')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # Add available languages to context
        context['available_languages'] = settings.LANGUAGES
        
        # Add current language to context for translation links
        context['current_language'] = get_language()
        
        # Create filters for the search component
        context['range_scale_filters'] = [
            {
                'type': 'select',
                'name': 'range_size',
                'label': 'Range size',
                'selected': self.request.GET.get('range_size', 'all'),
                'options': [
                    {'value': 'small', 'label': 'Small (≤10)'},
                    {'value': 'medium', 'label': 'Medium (11-100)'},
                    {'value': 'large', 'label': 'Large (>100)'}
                ],
                'trigger': 'hx-trigger="change"'
            },
            {
                'type': 'select',
                'name': 'has_increment',
                'label': 'Has increment',
                'selected': self.request.GET.get('has_increment', 'all'),
                'options': [
                    {'value': 'yes', 'label': 'Has increment'},
                    {'value': 'no', 'label': 'No increment'}
                ],
                'trigger': 'hx-trigger="change"'
            }
        ]
        
        return context
    
    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # For HTMX requests, let Django handle pagination normally
            # but just return the partial template
            response = super().get(request, *args, **kwargs)
            
            # If the superclass call resulted in a successful response
            if hasattr(response, 'context_data'):
                context = response.context_data
            else:
                # Fallback to getting context manually
                context = self.get_context_data()
            
            # Render only the table part for HTMX
            html = render_to_string('promapp/partials/range_scale_list_table.html', context, request=request)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)

class ConstructScaleListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    '''
    View to list all construct scales
    '''
    model = ConstructScale
    template_name = 'promapp/construct_scale_list.html'
    context_object_name = 'construct_scales'
    permission_required = 'promapp.view_constructscale'
    paginate_by = 25  # Show 25 items per page
    
    def get_queryset(self):
        queryset = ConstructScale.objects.all()
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(name__icontains=search)
        
        # Apply instrument name filter if provided
        instrument_name = self.request.GET.get('instrument_name')
        if instrument_name and instrument_name != 'all':
            queryset = queryset.filter(instrument_name__icontains=instrument_name)
        
        # Apply has equation filter if provided
        has_equation = self.request.GET.get('has_equation')
        if has_equation and has_equation != 'all':
            if has_equation == 'yes':
                queryset = queryset.exclude(scale_equation__isnull=True).exclude(scale_equation='')
            elif has_equation == 'no':
                queryset = queryset.filter(Q(scale_equation__isnull=True) | Q(scale_equation=''))
            
        return queryset.order_by('name')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # Get unique instrument names for filter
        instrument_names = ConstructScale.objects.exclude(
            instrument_name__isnull=True
        ).exclude(
            instrument_name=''
        ).values_list('instrument_name', flat=True).distinct().order_by('instrument_name')
        
        # Create filters for the search component
        context['construct_scale_filters'] = [
            {
                'type': 'select',
                'name': 'instrument_name',
                'label': 'Filter by instrument',
                'selected': self.request.GET.get('instrument_name', 'all'),
                'options': [{'value': name, 'label': name} for name in instrument_names],
                'trigger': 'hx-trigger="change"'
            },
            {
                'type': 'select',
                'name': 'has_equation',
                'label': 'Has equation',
                'selected': self.request.GET.get('has_equation', 'all'),
                'options': [
                    {'value': 'yes', 'label': 'Has equation'},
                    {'value': 'no', 'label': 'No equation'}
                ],
                'trigger': 'hx-trigger="change"'
            }
        ]
        
        return context
    
    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # For HTMX requests, let Django handle pagination normally
            # but just return the partial template
            response = super().get(request, *args, **kwargs)
            
            # If the superclass call resulted in a successful response
            if hasattr(response, 'context_data'):
                context = response.context_data
            else:
                # Fallback to getting context manually
                context = self.get_context_data()
            
            # Render only the table part for HTMX
            html = render_to_string('promapp/partials/construct_scale_list_table.html', context, request=request)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)

class QuestionnaireResponseView(LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin, DetailView):
    """
    View for handling questionnaire responses.
    This view allows patients to respond to questionnaires assigned to them.
    Only accessible to users with a Patient profile AND required permissions.
    """
    model = PatientQuestionnaire
    template_name = 'promapp/questionnaire_response.html'
    context_object_name = 'patient_questionnaire'
    permission_required = [
        'promapp.view_patientquestionnaire', 
        'promapp.add_questionnaireitemresponse', 
        'promapp.add_questionnairesubmission'
    ]
    
    def test_func(self):
        """
        Check if user has a Patient profile.
        This is checked AFTER permissions are verified.
        """
        return hasattr(self.request.user, 'patient') and Patient.objects.filter(user=self.request.user).exists()

    def get_queryset(self):
        # Only allow access to questionnaires assigned to the current patient
        return PatientQuestionnaire.objects.filter(patient=self.request.user.patient)

    def check_interval(self, patient_questionnaire):
        # Get the last submission for this questionnaire by this patient
        last_submission = QuestionnaireSubmission.objects.filter(
            patient_questionnaire=patient_questionnaire
        ).order_by('-submission_date').first()

        if last_submission:
            # Calculate time since last response
            time_since_last = timezone.now() - last_submission.submission_date
            interval = patient_questionnaire.questionnaire.questionnaire_answer_interval

            # Handle special case: if interval is 0, allow immediate re-answering
            if interval == 0:
                return True, None
            # Handle edge case: if interval is negative (shouldn't happen with validation), treat as 0
            elif interval < 0:
                return True, None
            
            # Check if enough time has passed since the last submission
            if time_since_last.total_seconds() < interval:
                return False, last_submission.submission_date + timezone.timedelta(seconds=interval)

        return True, None

    def dispatch(self, request, *args, **kwargs):
        # Check if the questionnaire can be answered before proceeding with any view logic
        self.object = self.get_object()
        can_answer, next_available = self.check_interval(self.object)
        
        logger = logging.getLogger("promapp.questionnaire_responses")
        logger.info(f"Dispatch for questionnaire {self.object.questionnaire.id} (order: {self.object.questionnaire.questionnaire_order}), can_answer: {can_answer}")
        
        if not can_answer:
            # If this questionnaire cannot be answered yet, try to find the next available one
            # This handles the case where a redirect brings us to a questionnaire that was recently completed
            next_questionnaire = self.get_next_available_questionnaire(self.object)
            
            if next_questionnaire and next_questionnaire.id != self.object.id:
                # Redirect to the next available questionnaire
                logger.info(f"Redirecting from questionnaire {self.object.questionnaire.id} to {next_questionnaire.questionnaire.id}")
                messages.info(request, _('Redirecting to the next available questionnaire.'))
                return redirect('questionnaire_response', pk=next_questionnaire.id)
            else:
                # No more questionnaires available, show the interval message
                logger.info(f"No more questionnaires available, redirecting to list")
                messages.warning(request, _('You cannot answer this questionnaire yet. You can answer it again in %(time)s.') % {
                    'time': timeuntil(next_available)
                })
                return redirect('my_questionnaire_list')
        
        return super().dispatch(request, *args, **kwargs)

    def get_translated_items(self, questionnaire):
        """Helper method to get questionnaire items with properly translated Likert options"""
        current_language = get_language()
        questionnaire_items = QuestionnaireItem.objects.filter(
            questionnaire=questionnaire
        ).select_related(
            'item',
            'item__likert_response',
            'item__range_response'
        ).prefetch_related(
            'item__likert_response__likertscaleresponseoption_set'
        ).order_by('question_number')
        
        # Prepare questionnaire items with translated Likert options
        items_with_translations = []
        for qi in questionnaire_items:
            item_data = {
                'questionnaire_item': qi,
                'translated_options': [],
                'translated_range_scale': None
            }
            
            # If this is a Likert type question, get translated options
            if qi.item.response_type == 'Likert' and qi.item.likert_response:
                try:
                    # Try to get options in current language
                    options = qi.item.likert_response.likertscaleresponseoption_set.language(current_language).order_by('option_order')
                except:
                    # Fallback to English or any available language
                    try:
                        options = qi.item.likert_response.likertscaleresponseoption_set.language('en-gb').order_by('option_order')
                    except:
                        # Last fallback to all options
                        options = qi.item.likert_response.likertscaleresponseoption_set.all().order_by('option_order')
                
                item_data['translated_options'] = options
            
            # If this is a Range type question, get translated range scale
            elif qi.item.response_type == 'Range' and qi.item.range_response:
                try:
                    # Try to get range scale in current language
                    range_scale = qi.item.range_response
                    range_scale.set_current_language(current_language)
                    item_data['translated_range_scale'] = range_scale
                except:
                    # Fallback to English or default language
                    try:
                        range_scale = qi.item.range_response
                        range_scale.set_current_language('en-gb')
                        item_data['translated_range_scale'] = range_scale
                    except:
                        # Last fallback to original range scale
                        item_data['translated_range_scale'] = qi.item.range_response
            
            items_with_translations.append(item_data)
        
        return questionnaire_items, items_with_translations

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questionnaire = self.object.questionnaire
        
        # Get all items for this questionnaire with translations
        questionnaire_items, items_with_translations = self.get_translated_items(questionnaire)
        
        # Create the form with the questionnaire items
        form = QuestionnaireResponseForm(questionnaire_items=questionnaire_items)
        
        context.update({
            'form': form,
            'questionnaire_items': questionnaire_items,
            'items_with_translations': items_with_translations,
            'can_answer': True
        })
        return context

    def get_next_available_questionnaire(self, current_patient_questionnaire):
        """
        Find the next available questionnaire that can be answered.
        Returns the PatientQuestionnaire object or None if no more questionnaires are available.
        """
        # Get all questionnaires for this patient, ordered by questionnaire_order
        patient_questionnaires = PatientQuestionnaire.objects.filter(
            patient=self.request.user.patient,
            display_questionnaire=True
        ).select_related('questionnaire').order_by('questionnaire__questionnaire_order')
        
        # Find questionnaires that come after the current one in the sequence
        current_order = current_patient_questionnaire.questionnaire.questionnaire_order
        next_questionnaires = patient_questionnaires.filter(
            questionnaire__questionnaire_order__gt=current_order
        )
        
        # Check each subsequent questionnaire to see if it can be answered
        for pq in next_questionnaires:
            can_answer, _ = self.check_interval(pq)
            if can_answer:
                return pq
        
        # No more available questionnaires in the sequence after current one
        return None

    def post(self, request, *args, **kwargs):
        logger = logging.getLogger("promapp.questionnaire_responses")
        
        # Check if user has permission to add responses
        if not request.user.has_perm('promapp.add_questionnaireitemresponse'):
            messages.error(request, _('You do not have permission to submit responses.'))
            return self.render_to_response(self.get_context_data())

        # Get the patient questionnaire
        patient_questionnaire = self.get_object()
        logger.info(f"POST request for questionnaire {patient_questionnaire.questionnaire.id} by patient {request.user.patient.id}")
        
        # Get all items for this questionnaire with translations
        questionnaire_items, items_with_translations = self.get_translated_items(patient_questionnaire.questionnaire)
        
        # Create the form with the questionnaire items and file uploads
        form = QuestionnaireResponseForm(request.POST, request.FILES, questionnaire_items=questionnaire_items)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    # Create a new submission record
                    submission = QuestionnaireSubmission.objects.create(
                        patient=request.user.patient,
                        patient_questionnaire=patient_questionnaire,
                        user_submitting_questionnaire=request.user
                    )
                    
                    # Create responses for all items, including unanswered ones
                    for qi in questionnaire_items:
                        response_value = form.cleaned_data.get(f'response_{qi.id}')
                        response_media = None
                        
                        # Handle media files for Media response type
                        if qi.item.response_type == 'Media':
                            # Check if there's a media file for this question
                            media_field_name = f'response_media_{qi.id}'
                            if media_field_name in request.FILES:
                                response_media = request.FILES[media_field_name]
                        
                        # Create record for every question, even if unanswered
                        QuestionnaireItemResponse.objects.create(
                            questionnaire_submission=submission,
                            questionnaire_item=qi,
                            response_value=str(response_value) if response_value is not None else None,
                            response_media=response_media
                        )
                    
                    # Construct scores will be calculated automatically by the post_save signal
                    # on QuestionnaireItemResponse (see models.py trigger_score_calculation_on_response)
                    
                    logger.info(f"Submission {submission.id} created successfully for questionnaire {patient_questionnaire.questionnaire.id}")
                    
                    # Find the next available questionnaire in sequence
                    next_questionnaire = self.get_next_available_questionnaire(patient_questionnaire)
                    
                    if next_questionnaire:
                        # Redirect to the next available questionnaire
                        logger.info(f"Redirecting to next questionnaire {next_questionnaire.questionnaire.id} (order: {next_questionnaire.questionnaire.questionnaire_order})")
                        messages.success(request, _('Your responses have been saved successfully. Please continue with the next questionnaire.'))
                        return redirect('questionnaire_response', pk=next_questionnaire.id)
                    else:
                        # No more questionnaires available, redirect to list
                        logger.info(f"No more questionnaires available, redirecting to list")
                        messages.success(request, _('Your responses have been saved successfully. You have completed all available questionnaires.'))
                        return redirect('my_questionnaire_list')
            except Exception as e:
                # Log detailed error but show generic message
                logger = logging.getLogger("promapp.questionnaire_responses")
                logger.error(f"Error saving questionnaire responses for patient {request.user.patient.id if hasattr(request.user, 'patient') else 'unknown'}: {str(e)}")
                messages.error(request, _('An error occurred while saving your responses. Please try again or contact support if the problem persists.'))
                # Pass items_with_translations in the context for error cases
                context = self.get_context_data(form=form)
                context['items_with_translations'] = items_with_translations
                return self.render_to_response(context)
        else:
            messages.error(request, _('There was an error saving your responses. Please try again.'))
            # Pass items_with_translations in the context for error cases
            context = self.get_context_data(form=form)
            context['items_with_translations'] = items_with_translations
            return self.render_to_response(context)

class PatientQuestionnaireManagementView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """
    View for managing questionnaires assigned to a patient.
    This view allows staff to assign/unassign questionnaires to patients.
    """
    model = Patient
    template_name = 'promapp/patient_questionnaire_management.html'
    context_object_name = 'patient'
    permission_required = 'promapp.add_patientquestionnaire'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        patient = self.get_object()
        
        # Get all questionnaires with proper translation handling
        current_language = get_language()
        
        # Get all questionnaires with their translations in the current language
        all_questionnaires = Questionnaire.objects.filter(
            translations__language_code=current_language
        ).distinct('id').order_by('id', 'translations__name')
        
        # Get currently assigned questionnaires
        assigned_questionnaires = PatientQuestionnaire.objects.filter(
            patient=patient
        ).select_related('questionnaire')
        
        # Create a list of questionnaires with their assignment status
        questionnaires_with_status = []
        for questionnaire in all_questionnaires:
            assigned = assigned_questionnaires.filter(questionnaire=questionnaire).first()
            questionnaires_with_status.append({
                'questionnaire': questionnaire,
                'is_assigned': bool(assigned),
                'is_displayed': assigned.display_questionnaire if assigned else False,
                'assigned_date': assigned.created_date if assigned else None,
                'patient_questionnaire_id': assigned.id if assigned else None
            })
        
        context['questionnaires_with_status'] = questionnaires_with_status
        return context
    
    def post(self, request, *args, **kwargs):
        patient = self.get_object()
        action = request.POST.get('action')
        questionnaire_id = request.POST.get('questionnaire_id')
        
        try:
            questionnaire = Questionnaire.objects.get(id=questionnaire_id)
            
            if action == 'assign':
                # Create new assignment
                PatientQuestionnaire.objects.create(
                    patient=patient,
                    questionnaire=questionnaire,
                    display_questionnaire=True
                )
                messages.success(request, _('Questionnaire assigned successfully.'))
                
            elif action == 'unassign':
                # Remove assignment
                PatientQuestionnaire.objects.filter(
                    patient=patient,
                    questionnaire=questionnaire
                ).delete()
                messages.success(request, _('Questionnaire unassigned successfully.'))
                
            elif action == 'toggle_display':
                # Toggle display status
                patient_questionnaire = PatientQuestionnaire.objects.get(
                    patient=patient,
                    questionnaire=questionnaire
                )
                patient_questionnaire.display_questionnaire = not patient_questionnaire.display_questionnaire
                patient_questionnaire.save()
                status = 'displayed' if patient_questionnaire.display_questionnaire else 'hidden'
                messages.success(request, _(f'Questionnaire is now {status}.'))
                
        except Questionnaire.DoesNotExist:
            messages.error(request, _('Questionnaire not found.'))
        except PatientQuestionnaire.DoesNotExist:
            messages.error(request, _('Questionnaire assignment not found.'))
        except Exception as e:
            messages.error(request, _('An error occurred. Please try again.'))
        
        return redirect('patient_questionnaire_management', pk=patient.id)

class PatientQuestionnaireListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    View for listing patients who have questionnaires assigned to them.
    """
    model = Patient
    template_name = 'promapp/patient_questionnaire_list.html'
    context_object_name = 'patients'
    permission_required = 'promapp.view_patientquestionnaire'
    paginate_by = 25

    def get_queryset(self):
        queryset = Patient.objects.select_related('user').all()
        
        # Apply search filter
        search_query = self.request.GET.get('search')
        if search_query:
            # Use exact match for secured fields
            queryset = queryset.filter(
                models.Q(name__exact=search_query) |
                models.Q(patient_id__exact=search_query)
            )
        
        # Apply questionnaire count filter
        questionnaire_count = self.request.GET.get('questionnaire_count')
        if questionnaire_count:
            if questionnaire_count == '0':
                queryset = queryset.annotate(
                    q_count=models.Count('patientquestionnaire', distinct=True)
                ).filter(q_count=0)
            elif questionnaire_count == '1-5':
                queryset = queryset.annotate(
                    q_count=models.Count('patientquestionnaire', distinct=True)
                ).filter(q_count__gte=1, q_count__lte=5)
            elif questionnaire_count == '6-10':
                queryset = queryset.annotate(
                    q_count=models.Count('patientquestionnaire', distinct=True)
                ).filter(q_count__gte=6, q_count__lte=10)
            elif questionnaire_count == '10+':
                queryset = queryset.annotate(
                    q_count=models.Count('patientquestionnaire', distinct=True)
                ).filter(q_count__gt=10)
        
        # Apply sorting
        sort_by = self.request.GET.get('sort', 'name')
        if sort_by == 'name':
            queryset = queryset.order_by('name')
        elif sort_by == '-name':
            queryset = queryset.order_by('-name')
        elif sort_by == 'questionnaire_count':
            queryset = queryset.annotate(
                q_count=models.Count('patientquestionnaire', distinct=True)
            ).order_by('q_count')
        elif sort_by == '-questionnaire_count':
            queryset = queryset.annotate(
                q_count=models.Count('patientquestionnaire', distinct=True)
            ).order_by('-q_count')
        
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        current_language = get_language()
        for patient in context['patients']:
            # Count only unique questionnaire assignments
            patient.questionnaire_count = PatientQuestionnaire.objects.filter(
                patient=patient
            ).values('questionnaire').distinct().count()
            
            # Get unique questionnaire names in current language using a subquery
            questionnaire_ids = PatientQuestionnaire.objects.filter(
                patient=patient
            ).values_list('questionnaire_id', flat=True).distinct()
            
            patient.questionnaire_names = list(
                Questionnaire.objects.filter(
                    id__in=questionnaire_ids,
                    translations__language_code=current_language
                ).values_list('translations__name', flat=True)
            )
            
        # Add dropdown options for filter components
        from django.utils.translation import gettext as _
        
        context['questionnaire_count_choices'] = [
            ('0', _('None')),
            ('1-5', _('1-5')),
            ('6-10', _('6-10')),
            ('10+', _('10+')),
        ]
        
        context['sort_choices'] = [
            ('name', _('Name')),
            ('-name', _('Name (Z-A)')),
            ('questionnaire_count', _('Questionnaire Count')),
            ('-questionnaire_count', _('Questionnaire Count (High-Low)')),
        ]
            
        # Add current filter values to context
        context['current_search'] = self.request.GET.get('search', '')
        context['current_questionnaire_count'] = self.request.GET.get('questionnaire_count', '')
        context['current_sort'] = self.request.GET.get('sort', 'name')
        return context

class MyQuestionnaireListView(LoginRequiredMixin, ListView):
    '''
    View to list all questionnaires for the logged-in patient
    '''
    model = PatientQuestionnaire
    template_name = 'promapp/my_questionnaire_list.html'
    context_object_name = 'patient_questionnaires'

    def get_queryset(self):
        # Only show questionnaires for the logged-in patient, ordered by questionnaire_order
        return PatientQuestionnaire.objects.filter(
            patient__user=self.request.user,
            display_questionnaire=True
        ).select_related('questionnaire').order_by('questionnaire__questionnaire_order')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['patient'] = getattr(self.request.user, 'patient', None)
        
        # Add information about when each questionnaire can be answered next
        for pq in context['patient_questionnaires']:
            # Get the last submission for this questionnaire
            last_submission = QuestionnaireSubmission.objects.filter(
                patient_questionnaire=pq
            ).order_by('-submission_date').first()
            
            # Store the last submission for display
            pq.last_submission = last_submission
            
            if last_submission:
                # Calculate when the questionnaire can be answered next
                interval_seconds = pq.questionnaire.questionnaire_answer_interval
                
                # Handle special case: if interval is 0, allow immediate re-answering
                if interval_seconds == 0:
                    pq.next_available = last_submission.submission_date
                    pq.can_answer = True
                # Handle edge case: if interval is negative (shouldn't happen with validation), treat as 0
                elif interval_seconds < 0:
                    pq.next_available = last_submission.submission_date
                    pq.can_answer = True
                else:
                    next_available = last_submission.submission_date + timezone.timedelta(seconds=interval_seconds)
                    pq.next_available = next_available
                    pq.can_answer = timezone.now() >= next_available
            else:
                # If no previous submission, can answer immediately
                pq.next_available = None
                pq.can_answer = True
        
        return context

class QuestionnaireItemRuleListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    View for listing rules associated with a questionnaire item.
    """
    model = QuestionnaireItemRule
    template_name = 'promapp/questionnaire_item_rules_list.html'
    context_object_name = 'rules'
    permission_required = 'promapp.view_questionnaireitemrule'

    def get_queryset(self):
        questionnaire_item = get_object_or_404(QuestionnaireItem, pk=self.kwargs['questionnaire_item_id'])
        return QuestionnaireItemRule.objects.filter(
            questionnaire_item=questionnaire_item
        ).order_by('rule_order')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questionnaire_item = get_object_or_404(
            QuestionnaireItem, 
            pk=self.kwargs['questionnaire_item_id']
        )
        context['questionnaire_item'] = questionnaire_item
        context['is_required_item'] = questionnaire_item.item.is_required
        return context

class QuestionnaireItemRuleCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    View for creating a new rule for a questionnaire item.
    """
    model = QuestionnaireItemRule
    form_class = QuestionnaireItemRuleForm
    template_name = 'promapp/questionnaire_item_rule_form.html'
    permission_required = 'promapp.add_questionnaireitemrule'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        questionnaire_item = get_object_or_404(QuestionnaireItem, pk=self.kwargs['questionnaire_item_id'])
        kwargs['initial'] = kwargs.get('initial', {})
        kwargs['initial']['questionnaire_item'] = questionnaire_item
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.instance.questionnaire_item = get_object_or_404(
            QuestionnaireItem, 
            pk=self.kwargs['questionnaire_item_id']
        )
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questionnaire_item = get_object_or_404(
            QuestionnaireItem, 
            pk=self.kwargs['questionnaire_item_id']
        )
        context['questionnaire_item'] = questionnaire_item
        context['is_required_item'] = questionnaire_item.item.is_required
        return context

    def get_success_url(self):
        return reverse('questionnaire_item_rules_list', 
                      kwargs={'questionnaire_item_id': self.kwargs['questionnaire_item_id']})

class QuestionnaireItemRuleUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View for updating an existing rule.
    """
    model = QuestionnaireItemRule
    form_class = QuestionnaireItemRuleForm
    template_name = 'promapp/questionnaire_item_rule_form.html'
    permission_required = 'promapp.change_questionnaireitemrule'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['questionnaire_item'] = self.object.questionnaire_item
        context['is_required_item'] = self.object.questionnaire_item.item.is_required
        return context

    def get_success_url(self):
        return reverse('questionnaire_item_rules_list', 
                      kwargs={'questionnaire_item_id': self.object.questionnaire_item.id})

class QuestionnaireItemRuleDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    View for deleting a rule.
    """
    model = QuestionnaireItemRule
    permission_required = 'promapp.delete_questionnaireitemrule'

    def get_success_url(self):
        return reverse('questionnaire_item_rules_list', 
                      kwargs={'questionnaire_item_id': self.object.questionnaire_item.id})

class QuestionnaireItemRuleGroupListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    View for listing rule groups associated with a questionnaire item.
    """
    model = QuestionnaireItemRuleGroup
    template_name = 'promapp/questionnaire_item_rule_groups_list.html'
    context_object_name = 'rule_groups'
    permission_required = 'promapp.view_questionnaireitemrulegroup'

    def get_queryset(self):
        questionnaire_item = get_object_or_404(QuestionnaireItem, pk=self.kwargs['questionnaire_item_id'])
        return QuestionnaireItemRuleGroup.objects.filter(
            questionnaire_item=questionnaire_item
        ).order_by('group_order')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questionnaire_item = get_object_or_404(
            QuestionnaireItem, 
            pk=self.kwargs['questionnaire_item_id']
        )
        context['questionnaire_item'] = questionnaire_item
        context['is_required_item'] = questionnaire_item.item.is_required
        return context

class QuestionnaireItemRuleGroupCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    View for creating a new rule group.
    """
    model = QuestionnaireItemRuleGroup
    form_class = QuestionnaireItemRuleGroupForm
    template_name = 'promapp/questionnaire_item_rule_group_form.html'
    permission_required = 'promapp.add_questionnaireitemrulegroup'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        questionnaire_item = get_object_or_404(QuestionnaireItem, pk=self.kwargs['questionnaire_item_id'])
        kwargs['initial'] = kwargs.get('initial', {})
        kwargs['initial']['questionnaire_item'] = questionnaire_item
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.instance.questionnaire_item = get_object_or_404(
            QuestionnaireItem, 
            pk=self.kwargs['questionnaire_item_id']
        )
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questionnaire_item = get_object_or_404(
            QuestionnaireItem, 
            pk=self.kwargs['questionnaire_item_id']
        )
        context['questionnaire_item'] = questionnaire_item
        context['is_required_item'] = questionnaire_item.item.is_required
        context['available_rules'] = QuestionnaireItemRule.objects.filter(
            questionnaire_item=questionnaire_item
        ).order_by('rule_order')
        return context

    def get_success_url(self):
        return reverse('questionnaire_item_rule_groups_list', 
                      kwargs={'questionnaire_item_id': self.kwargs['questionnaire_item_id']})

class QuestionnaireItemRuleGroupUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View for updating an existing rule group.
    """
    model = QuestionnaireItemRuleGroup
    form_class = QuestionnaireItemRuleGroupForm
    template_name = 'promapp/questionnaire_item_rule_group_form.html'
    permission_required = 'promapp.change_questionnaireitemrulegroup'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['questionnaire_item'] = self.object.questionnaire_item
        context['is_required_item'] = self.object.questionnaire_item.item.is_required
        context['available_rules'] = QuestionnaireItemRule.objects.filter(
            questionnaire_item=self.object.questionnaire_item
        ).order_by('rule_order')
        return context

    def get_success_url(self):
        return reverse('questionnaire_item_rule_groups_list', 
                      kwargs={'questionnaire_item_id': self.object.questionnaire_item.id})

class QuestionnaireItemRuleGroupDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    View for deleting a rule group.
    """
    model = QuestionnaireItemRuleGroup
    permission_required = 'promapp.delete_questionnaireitemrulegroup'

    def get_success_url(self):
        return reverse('questionnaire_item_rule_groups_list', 
                      kwargs={'questionnaire_item_id': self.object.questionnaire_item.id})

# Export Questionnaire Responses Views
class QuestionnaireExportListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """View for listing questionnaires available for export."""
    model = Questionnaire
    template_name = 'promapp/questionnaire_export_list.html'
    context_object_name = 'questionnaires'
    permission_required = 'promapp.view_questionnaire'
    paginate_by = 10

    def get_queryset(self):
        """Return only questionnaires that have submissions."""
        queryset = super().get_queryset()
        
        # Filter questionnaires that have at least one submission
        questionnaires_with_submissions = QuestionnaireSubmission.objects.values_list(
            'patient_questionnaire__questionnaire', flat=True
        ).distinct()
        
        queryset = queryset.filter(id__in=questionnaires_with_submissions)
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(translations__name__icontains=search)
            
        return queryset.distinct('id').order_by('id', 'translations__name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        return context


class QuestionnaireExportPatientListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """View for listing patients who have submitted a specific questionnaire."""
    model = Patient
    template_name = 'promapp/questionnaire_export_patient_list.html'
    context_object_name = 'patients'
    permission_required = 'promapp.view_questionnaire'
    paginate_by = 25

    def get_queryset(self):
        """Return patients who have submitted the selected questionnaire."""
        questionnaire_id = self.kwargs.get('questionnaire_id')
        self.questionnaire = get_object_or_404(Questionnaire, id=questionnaire_id)
        
        # Get patients who have submitted this questionnaire
        patient_ids = QuestionnaireSubmission.objects.filter(
            patient_questionnaire__questionnaire=self.questionnaire
        ).values_list('patient', flat=True).distinct()
        
        # Filter patients by institution if user is not admin
        queryset = Patient.objects.filter(id__in=patient_ids)
        
        # If user is not admin, filter by their institution
        if not self.request.user.is_superuser:
            # Assuming there's a relationship between user and institution
            user_institutions = self.request.user.institutions.all()
            queryset = queryset.filter(institution__in=user_institutions)
            
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) | 
                Q(last_name__icontains=search) |
                Q(patient_id__icontains=search)
            )
            
        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['questionnaire'] = self.questionnaire
        context['search_query'] = self.request.GET.get('search', '')
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # For each patient, get the number of submissions they have for this questionnaire
        patients_with_submission_count = []
        for patient in context['patients']:
            submission_count = QuestionnaireSubmission.objects.filter(
                patient=patient,
                patient_questionnaire__questionnaire=self.questionnaire
            ).count()
            
            patients_with_submission_count.append({
                'patient': patient,
                'submission_count': submission_count,
            })
            
        context['patients_with_submission_count'] = patients_with_submission_count
        
        return context


@login_required
@permission_required('promapp.view_questionnaireitemresponse')
def export_questionnaire_responses(request, questionnaire_id, patient_id=None):
    """Function for exporting questionnaire responses to CSV."""
    questionnaire = get_object_or_404(Questionnaire, id=questionnaire_id)
    
    # Set up the CSV response
    http_response = HttpResponse(content_type='text/csv')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"questionnaire_responses_{questionnaire.name}_{timestamp}.csv"
    http_response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    # Create CSV writer
    writer = csv.writer(http_response)
    
    # Get all items for this questionnaire
    questionnaire_items = QuestionnaireItem.objects.filter(
        questionnaire=questionnaire
    ).select_related('item').order_by('question_number')
    
    # Create header row
    header = [
        _('Patient ID'), 
        _('Institution'), 
        _('Submission Date')
    ]
    
    # Add column for each questionnaire item
    for qi in questionnaire_items:
        if qi.item.response_type in [
            ResponseTypeChoices.TEXT, 
            ResponseTypeChoices.NUMBER,
            ResponseTypeChoices.LIKERT,
            ResponseTypeChoices.RANGE
        ]:
            # Use only abbreviated_item_id in the header if available, otherwise use question number and name
            if qi.item.abbreviated_item_id:
                header.append(qi.item.abbreviated_item_id)
            else:
                item_name = f"{qi.question_number}. {qi.item.name}" if qi.question_number else qi.item.name
                header.append(item_name)
    
    writer.writerow(header)
    
    # Get submissions
    submissions_query = QuestionnaireSubmission.objects.filter(
        patient_questionnaire__questionnaire=questionnaire
    ).select_related('patient', 'patient__institution')
    
    # Filter by patient if specified
    if patient_id:
        patient = get_object_or_404(Patient, id=patient_id)
        submissions_query = submissions_query.filter(patient=patient)
        
    # Filter by institution if user is not admin
    if not request.user.is_superuser:
        user_institutions = request.user.institutions.all()
        submissions_query = submissions_query.filter(patient__institution__in=user_institutions)
        
    submissions = submissions_query.order_by('-submission_date')
    
    # Write data rows
    for submission in submissions:
        # Get all responses for this submission
        item_responses = QuestionnaireItemResponse.objects.filter(
            questionnaire_submission=submission
        ).select_related('questionnaire_item', 'questionnaire_item__item')
        
        # Create a mapping of questionnaire item IDs to responses
        response_map = {r.questionnaire_item.id: r for r in item_responses}
        
        # Create row with patient info and submission date
        row = [
            submission.patient.patient_id,  # Encrypted patient ID
            submission.patient.institution.name if submission.patient.institution else '',
            submission.submission_date.strftime('%Y-%m-%d %H:%M:%S')
        ]
        
        # Add response for each questionnaire item
        for qi in questionnaire_items:
            if qi.item.response_type not in [
                ResponseTypeChoices.TEXT, 
                ResponseTypeChoices.NUMBER,
                ResponseTypeChoices.LIKERT,
                ResponseTypeChoices.RANGE
            ]:
                continue
                
            item_response = response_map.get(qi.id)
            if item_response:
                # All response types use the response_value field
                row.append(item_response.response_value or '')
            else:
                row.append('')  # Empty cell for no response
                
        writer.writerow(row)
    
    return http_response

# HTMX Views for Rule Forms
def validate_dependent_item(request):
    """Validate the selected dependent item and return appropriate feedback."""
    item_id = request.GET.get('dependent_item')
    if not item_id:
        return HttpResponse(_("Please select a dependent item."))
    
    try:
        item = QuestionnaireItem.objects.get(id=item_id)
        return HttpResponse(_("Selected item: {}").format(item.item.name))
    except QuestionnaireItem.DoesNotExist:
        return HttpResponse(_("Invalid item selected."))

def validate_rule_operator(request):
    """Validate the selected operator and return appropriate feedback."""
    operator = request.GET.get('operator')
    dependent_item_id = request.GET.get('dependent_item')
    
    if not operator:
        return HttpResponse(_("Please select an operator."))
    
    try:
        dependent_item = QuestionnaireItem.objects.get(id=dependent_item_id)
        response_type = dependent_item.item.response_type
        
        # Return appropriate feedback based on response type
        if response_type in ['Number', 'Likert', 'Range']:
            return HttpResponse(_("Valid operator for numeric comparison."))
        else:
            return HttpResponse(_("Valid operator for text comparison."))
    except QuestionnaireItem.DoesNotExist:
        return HttpResponse(_("Invalid dependent item."))

def validate_comparison_value(request):
    """Validate the comparison value based on the dependent item's response type."""
    value = request.GET.get('comparison_value')
    dependent_item_id = request.GET.get('dependent_item')
    
    if not value:
        return HttpResponse(_("Please enter a comparison value."))
    
    try:
        dependent_item = QuestionnaireItem.objects.get(id=dependent_item_id)
        response_type = dependent_item.item.response_type
        
        if response_type == 'Number':
            try:
                float(value)
                return HttpResponse(_("Valid numeric value."))
            except ValueError:
                return HttpResponse(_("Please enter a valid number."))
        elif response_type == 'Likert':
            try:
                float_value = float(value)
                likert_options = dependent_item.item.likert_response.likertscaleresponseoption_set.all()
                valid_values = [option.option_value for option in likert_options]
                if float_value in valid_values:
                    return HttpResponse(_("Valid Likert scale value."))
                else:
                    return HttpResponse(_("Please enter a valid Likert scale value."))
            except ValueError:
                return HttpResponse(_("Please enter a valid number."))
        elif response_type == 'Range':
            try:
                float_value = float(value)
                range_scale = dependent_item.item.range_response
                if range_scale.min_value <= float_value <= range_scale.max_value:
                    return HttpResponse(_("Valid range value."))
                else:
                    return HttpResponse(_("Value must be between {} and {}.").format(
                        range_scale.min_value, range_scale.max_value))
            except ValueError:
                return HttpResponse(_("Please enter a valid number."))
        else:
            return HttpResponse(_("Valid text value."))
    except QuestionnaireItem.DoesNotExist:
        return HttpResponse(_("Invalid dependent item."))

def validate_logical_operator(request):
    """Validate the logical operator selection."""
    operator = request.GET.get('logical_operator')
    if not operator:
        return HttpResponse(_("Please select a logical operator."))
    return HttpResponse(_("Valid logical operator."))

def validate_rule_order(request):
    """Validate the rule order."""
    order = request.GET.get('rule_order')
    if not order:
        return HttpResponse(_("Please enter a rule order."))
    try:
        order_num = int(order)
        if order_num < 1:
            return HttpResponse(_("Order must be greater than 0."))
        return HttpResponse(_("Valid rule order."))
    except ValueError:
        return HttpResponse(_("Please enter a valid number."))

def validate_group_order(request):
    """Validate the group order."""
    order = request.GET.get('group_order')
    if not order:
        return HttpResponse(_("Please enter a group order."))
    try:
        order_num = int(order)
        if order_num < 1:
            return HttpResponse(_("Order must be greater than 0."))
        return HttpResponse(_("Valid group order."))
    except ValueError:
        return HttpResponse(_("Please enter a valid number."))

def validate_rule_selection(request):
    """Validate the selected rules for a rule group."""
    rule_ids = request.GET.getlist('rules')
    if not rule_ids:
        return HttpResponse(_("Please select at least one rule."))
    
    try:
        rules = QuestionnaireItemRule.objects.filter(id__in=rule_ids)
        if len(rules) != len(rule_ids):
            return HttpResponse(_("One or more invalid rules selected."))
        
        # Check if all rules belong to the same questionnaire item
        questionnaire_items = set(rule.questionnaire_item for rule in rules)
        if len(questionnaire_items) > 1:
            return HttpResponse(_("All rules must belong to the same questionnaire item."))
        
        return HttpResponse(_("Valid rule selection."))
    except Exception:
        return HttpResponse(_("Error validating rules."))

def rule_summary(request, questionnaire_item_id):
    """Return a summary of rules for a questionnaire item."""
    questionnaire_item = get_object_or_404(QuestionnaireItem, pk=questionnaire_item_id)
    rules = QuestionnaireItemRule.objects.filter(
        questionnaire_item=questionnaire_item
    ).order_by('rule_order')
    
    return render(request, 'promapp/partials/rule_summary.html', {
        'rules': rules
    })

def rule_group_summary(request, questionnaire_item_id):
    """Return a summary of rule groups for a questionnaire item."""
    questionnaire_item = get_object_or_404(QuestionnaireItem, pk=questionnaire_item_id)
    rule_groups = QuestionnaireItemRuleGroup.objects.filter(
        questionnaire_item=questionnaire_item
    ).order_by('group_order')
    
    return render(request, 'promapp/partials/rule_group_summary.html', {
        'rule_groups': rule_groups
    })

def save_question_numbers(request, pk):
    """View to handle saving question numbers via AJAX."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method. Please use POST to update question numbers.'})
    
    try:
        questionnaire = get_object_or_404(Questionnaire, pk=pk)
        
        # Parse the JSON data
        data = json.loads(request.body)
        question_numbers = data.get('question_numbers', {})
        removed_items = data.get('removed_items', [])
        
        if not question_numbers and not removed_items:
            return JsonResponse({
                'success': False,
                'error': 'No changes provided. Please select questions to update or remove.'
            })
        
        # Get all questionnaire items
        questionnaire_items = QuestionnaireItem.objects.filter(
            questionnaire=questionnaire
        ).select_related('item')
        
        # Create a mapping of item IDs to questionnaire items
        item_map = {str(qi.item.id): qi for qi in questionnaire_items}
        
        # Track used question numbers
        used_numbers = set()
        
        # First pass: Validate all changes
        for item_id, new_number in question_numbers.items():
            if new_number in used_numbers:
                return JsonResponse({
                    'success': False,
                    'error': f'Duplicate question number {new_number} detected. Each question must have a unique number.'
                })
            used_numbers.add(new_number)
            
            if item_id in item_map:
                qi = item_map[item_id]
                if qi.question_number != new_number:
                    # Check for rule conflicts
                    affected_rules = QuestionnaireItemRule.objects.filter(
                        models.Q(
                            questionnaire_item=qi,
                            dependent_item__question_number__gte=new_number
                        ) |
                        models.Q(
                            dependent_item=qi,
                            questionnaire_item__question_number__lte=new_number
                        )
                    )
                    if affected_rules.exists():
                        rule_details = []
                        for rule in affected_rules:
                            rule_details.append(
                                f"- Rule for question '{rule.questionnaire_item.item.name}' "
                                f"based on question '{rule.dependent_item.item.name}'"
                            )
                        return JsonResponse({
                            'success': False,
                            'error': f'Cannot change question number for "{qi.item.name}" as it would invalidate the following rules:\n' + '\n'.join(rule_details)
                        })
                    
                    # Check for construct scale equation conflicts
                    ref_check = qi.item.is_referenced_in_equation()
                    if ref_check['is_referenced']:
                        return JsonResponse({
                            'success': False,
                            'error': f'Cannot change question number for "{qi.item.name}" as it is referenced in the construct scale equation "{ref_check["equation"]}" for scale "{ref_check["construct_name"]}". Please update the equation first.'
                        })
        
        # Second pass: Apply all changes
        with transaction.atomic():
            # Update question numbers for remaining items
            for item_id, new_number in question_numbers.items():
                if item_id in item_map:
                    qi = item_map[item_id]
                    if qi.question_number != new_number:
                        qi.question_number = new_number
                        qi.save()
            
            # Remove items that are no longer in the list
            for item_id in removed_items:
                if item_id in item_map:
                    qi = item_map[item_id]
                    # Check if there are any rules depending on this item
                    dependent_rules = QuestionnaireItemRule.objects.filter(
                        dependent_item=qi
                    )
                    if dependent_rules.exists():
                        rule_details = []
                        for rule in dependent_rules:
                            rule_details.append(
                                f"- Question '{rule.questionnaire_item.item.name}' depends on this question"
                            )
                        return JsonResponse({
                            'success': False,
                            'error': f'Cannot remove question "{qi.item.name}" as it is referenced by the following rules:\n' + '\n'.join(rule_details)
                        })
                    # Check if this item has any rules
                    item_rules = QuestionnaireItemRule.objects.filter(
                        questionnaire_item=qi
                    )
                    if item_rules.exists():
                        rule_details = []
                        for rule in item_rules:
                            rule_details.append(
                                f"- Rule based on question '{rule.dependent_item.item.name}'"
                            )
                        return JsonResponse({
                            'success': False,
                            'error': f'Cannot remove question "{qi.item.name}" as it has the following rules:\n' + '\n'.join(rule_details)
                        })
                    
                    # Check if this item is referenced in any construct scale equations
                    ref_check = qi.item.is_referenced_in_equation()
                    if ref_check['is_referenced']:
                        return JsonResponse({
                            'success': False,
                            'error': f'Cannot remove question "{qi.item.name}" as it is referenced in the construct scale equation "{ref_check["equation"]}" for scale "{ref_check["construct_name"]}". Please update the equation first.'
                        })
        
        return JsonResponse({
            'success': True,
            'message': 'Question numbers updated successfully. All changes have been saved.'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data. Please check the format of your request.'
        })
    except Questionnaire.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Questionnaire not found. Please refresh the page and try again.'
        })
    except Exception as e:
        # Log the detailed error for debugging but return generic message
        logger = logging.getLogger("promapp.questionnaire_management")
        logger.error(f"Unexpected error saving question numbers for questionnaire {questionnaire.id if 'questionnaire' in locals() else 'unknown'}: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'An unexpected error occurred while saving question numbers. Please try again or contact support if the problem persists.'
        })

class QuestionnaireRulesView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """
    View for managing rules for a questionnaire.
    """
    model = Questionnaire
    template_name = 'promapp/questionnaire_rules.html'
    context_object_name = 'questionnaire'
    permission_required = 'promapp.add_questionnaire'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questionnaire = self.get_object()
        
        # Prefetch rules and rule groups for all questionnaire items
        raw_items = QuestionnaireItem.objects.filter(
            questionnaire=questionnaire
        ).order_by('question_number').prefetch_related(
            Prefetch('visibility_rules', queryset=QuestionnaireItemRule.objects.order_by('rule_order')),
            Prefetch('rule_groups', queryset=QuestionnaireItemRuleGroup.objects.order_by('group_order').prefetch_related('rules'))
        )
        
        questionnaire_items_structured = []
        for item in raw_items:
            rules = list(item.visibility_rules.all())
            groups = list(item.rule_groups.all())
            grouped_rule_ids = set()
            for group in groups:
                grouped_rule_ids.update(r.id for r in group.rules.all())
            ungrouped_rules = [r for r in rules if r.id not in grouped_rule_ids]
            questionnaire_items_structured.append({
                'item': item,
                'rules': ungrouped_rules,
                'rule_groups': groups,
            })
        
        context['questionnaire_items_structured'] = questionnaire_items_structured
        return context

def evaluate_question_rules(request, questionnaire_item_id):
    """
    View to evaluate rules for a questionnaire item.
    Returns JSON response indicating whether the question should be shown.
    """
    logger = logging.getLogger("promapp.rules")
    try:
        questionnaire_item = get_object_or_404(QuestionnaireItem, pk=questionnaire_item_id)
        responses = json.loads(request.body)
        logger.info(f"Evaluating rules for QuestionnaireItem {questionnaire_item_id} with responses: {responses}")
        
        # Get all rules and rule groups for this item
        rules = questionnaire_item.visibility_rules.all()
        rule_groups = questionnaire_item.rule_groups.all()
        
        # If no rules or groups, always show the question
        if not rules and not rule_groups:
            logger.info(f"No rules or rule groups for QuestionnaireItem {questionnaire_item_id}. Showing question.")
            return JsonResponse({'should_show': True})
        
        # Evaluate individual rules
        rule_results = []
        for rule in rules:
            dependent_response = responses.get(str(rule.dependent_item.id))
            logger.info(f"Evaluating rule: Dependent Q{rule.dependent_item.question_number}, Operator: {rule.operator}, Comparison: {rule.comparison_value}, User Response: {dependent_response}")
            if dependent_response is None:
                logger.info(f"No response for dependent item {rule.dependent_item.id}. Skipping rule.")
                continue
            
            try:
                if rule.dependent_item.item.response_type in ['Number', 'Likert', 'Range']:
                    dependent_value = float(dependent_response)
                    comparison_value = float(rule.comparison_value)
                else:
                    dependent_value = str(dependent_response)
                    comparison_value = str(rule.comparison_value)
                
                result = False
                if rule.operator == 'EQUALS':
                    result = dependent_value == comparison_value
                elif rule.operator == 'NOT_EQUALS':
                    result = dependent_value != comparison_value
                elif rule.operator == 'GREATER_THAN':
                    result = dependent_value > comparison_value
                elif rule.operator == 'LESS_THAN':
                    result = dependent_value < comparison_value
                elif rule.operator == 'GREATER_THAN_EQUALS':
                    result = dependent_value >= comparison_value
                elif rule.operator == 'LESS_THAN_EQUALS':
                    result = dependent_value <= comparison_value
                elif rule.operator == 'CONTAINS':
                    result = str(comparison_value) in str(dependent_value)
                elif rule.operator == 'NOT_CONTAINS':
                    result = str(comparison_value) not in str(dependent_value)
                logger.info(f"Rule result: {result}")
                rule_results.append((result, rule.logical_operator))
            except (ValueError, TypeError) as e:
                logger.warning(f"Error evaluating rule: {e}")
                continue
        
        # Evaluate rule groups
        group_results = []
        for group in rule_groups:
            group_rules = group.rules.all()
            if not group_rules:
                continue
            group_result = True
            for i, rule in enumerate(group_rules):
                dependent_response = responses.get(str(rule.dependent_item.id))
                logger.info(f"[Group {group.group_order}] Evaluating rule: Dependent Q{rule.dependent_item.question_number}, Operator: {rule.operator}, Comparison: {rule.comparison_value}, User Response: {dependent_response}")
                if dependent_response is None:
                    logger.info(f"[Group {group.group_order}] No response for dependent item {rule.dependent_item.id}. Skipping rule.")
                    continue
                try:
                    if rule.dependent_item.item.response_type in ['Number', 'Likert', 'Range']:
                        dependent_value = float(dependent_response)
                        comparison_value = float(rule.comparison_value)
                    else:
                        dependent_value = str(dependent_response)
                        comparison_value = str(rule.comparison_value)
                    result = False
                    if rule.operator == 'EQUALS':
                        result = dependent_value == comparison_value
                    elif rule.operator == 'NOT_EQUALS':
                        result = dependent_value != comparison_value
                    elif rule.operator == 'GREATER_THAN':
                        result = dependent_value > comparison_value
                    elif rule.operator == 'LESS_THAN':
                        result = dependent_value < comparison_value
                    elif rule.operator == 'GREATER_THAN_EQUALS':
                        result = dependent_value >= comparison_value
                    elif rule.operator == 'LESS_THAN_EQUALS':
                        result = dependent_value <= comparison_value
                    elif rule.operator == 'CONTAINS':
                        result = str(comparison_value) in str(dependent_value)
                    elif rule.operator == 'NOT_CONTAINS':
                        result = str(comparison_value) not in str(dependent_value)
                    logger.info(f"[Group {group.group_order}] Rule result: {result}")
                    if i > 0:
                        if rule.logical_operator == 'AND':
                            group_result = group_result and result
                        else:  # OR
                            group_result = group_result or result
                    else:
                        group_result = result
                except (ValueError, TypeError) as e:
                    logger.warning(f"[Group {group.group_order}] Error evaluating rule: {e}")
                    continue
            group_results.append(group_result)
        
        # Combine all results
        should_show = True
        if rule_results:
            current_result = rule_results[0][0]
            for i in range(1, len(rule_results)):
                result, operator = rule_results[i]
                if operator == 'AND':
                    current_result = current_result and result
                else:  # OR
                    current_result = current_result or result
            should_show = should_show and current_result
        if group_results:
            group_result = group_results[0]
            for result in group_results[1:]:
                group_result = group_result or result
            should_show = should_show and group_result
        logger.info(f"Final should_show for QuestionnaireItem {questionnaire_item_id}: {should_show}")
        return JsonResponse({'should_show': should_show})
    except Exception as e:
        logger.error(f"Error in evaluate_question_rules: {e}")
        return JsonResponse({'error': 'Unable to evaluate question rules. Please check your form data and try again.'}, status=400)

# Translation Views
class ItemTranslationView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View for managing translations of an Item.
    """
    model = Item
    form_class = ItemTranslationForm
    template_name = 'promapp/item_translation_form.html'
    permission_required = 'promapp.add_item'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_languages'] = settings.LANGUAGES
        context['current_language'] = self.request.GET.get('language', settings.LANGUAGE_CODE)
        item = self.get_object()
        context['original_name'] = item.name
        context['original_media'] = item.media
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        current_language = self.request.GET.get('language', settings.LANGUAGE_CODE)
        item = self.get_object()
        try:
            translation = item.translations.get(language_code=current_language)
            kwargs['initial'] = {
                'name': translation.name,
                'media': translation.media
            }
        except item.translations.model.DoesNotExist:
            kwargs['initial'] = {
                'name': '',
                'media': ''
            }
        return kwargs

    def form_valid(self, form):
        current_language = self.request.GET.get('language', settings.LANGUAGE_CODE)
        item = self.get_object()
        item.set_current_language(current_language)
        item.name = form.cleaned_data['name']
        item.media = form.cleaned_data['media']
        item.save()
        messages.success(self.request, _('Translation saved successfully.'))
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('item_list')

class ItemTranslationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    View for listing items with translation links.
    """
    model = Item
    template_name = 'promapp/item_translation_list.html'
    context_object_name = 'items'
    permission_required = 'promapp.add_item'

    def get_queryset(self):
        # Get all items with their translations in the default language
        queryset = Item.objects.all()
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(translations__name__icontains=search)
        
        # Apply language filter if provided
        language_filter = self.request.GET.get('language_filter')
        if language_filter:
            if language_filter.endswith('_translated'):
                # Filter for items that have translation in the specified language
                lang_code = language_filter.replace('_translated', '')
                queryset = queryset.filter(
                    translations__language_code=lang_code
                ).annotate(
                    has_content=models.Case(
                        models.When(
                            models.Q(translations__name__isnull=False) & 
                            ~models.Q(translations__name='') |
                            models.Q(translations__media__isnull=False) & 
                            ~models.Q(translations__media=''),
                            then=models.Value(True)
                        ),
                        default=models.Value(False),
                        output_field=models.BooleanField()
                    )
                ).filter(has_content=True)
            elif language_filter.endswith('_untranslated'):
                # Filter for items that don't have translation in the specified language
                lang_code = language_filter.replace('_untranslated', '')
                # Get items that either don't have translation or have empty translation
                translated_item_ids = Item.objects.filter(
                    translations__language_code=lang_code,
                    translations__name__isnull=False,
                ).exclude(
                    translations__name=''
                ).values_list('id', flat=True)
                
                translated_media_item_ids = Item.objects.filter(
                    translations__language_code=lang_code,
                    translations__media__isnull=False,
                ).exclude(
                    translations__media=''
                ).values_list('id', flat=True)
                
                # Combine both lists to get all translated items
                all_translated_ids = set(list(translated_item_ids) + list(translated_media_item_ids))
                
                queryset = queryset.exclude(id__in=all_translated_ids)
            
        # Prefetch translations for better performance
        queryset = queryset.prefetch_related('translations')
        
        return queryset.distinct().order_by('id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_languages'] = settings.LANGUAGES
        context['current_language'] = self.request.GET.get('language', settings.LANGUAGE_CODE)
        
        # Create form with current values
        form_initial = {
            'search': self.request.GET.get('search', ''),
            'language_filter': self.request.GET.get('language_filter', '')
        }
        context['search_form'] = TranslationSearchForm(initial=form_initial)
        
        # Set up HTMX attributes for both fields
        context['search_form'].fields['search'].widget.attrs['hx-get'] = reverse('item_translation_list')
        context['search_form'].fields['language_filter'].widget.attrs['hx-get'] = reverse('item_translation_list')
        
        # Add current filter values for form state preservation
        context['current_search'] = self.request.GET.get('search', '')
        context['current_language_filter'] = self.request.GET.get('language_filter', '')
        
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # Add translation status data for each item
        items_with_translation_status = []
        for item in context['items']:
            # Get all existing translations for this item
            existing_translations = set(
                item.translations.values_list('language_code', flat=True)
            )
            
            translation_status = []
            for lang_code, lang_name in settings.LANGUAGES:
                has_translation = lang_code in existing_translations
                # Check if translation has content (not just empty strings)
                if has_translation:
                    try:
                        translation = item.translations.get(language_code=lang_code)
                        has_content = bool(
                            (translation.name and translation.name.strip()) or 
                            (translation.media and str(translation.media).strip())
                        )
                    except item.translations.model.DoesNotExist:
                        has_content = False
                else:
                    has_content = False
                    
                translation_status.append({
                    'language_code': lang_code,
                    'language_name': lang_name,
                    'has_translation': has_translation and has_content,
                    'url': reverse('item_translation', args=[item.id]) + f'?language={lang_code}'
                })
            
            items_with_translation_status.append({
                'item': item,
                'translation_status': translation_status
            })
        
        context['items_with_translation_status'] = items_with_translation_status
        return context
    
    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # If it is an HTMX request, only return the table part
            self.object_list = self.get_queryset()
            context = self.get_context_data()
            html = render_to_string('promapp/partials/item_translation_list_table.html', context)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)

class QuestionnaireTranslationView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View for managing translations of a Questionnaire.
    """
    model = Questionnaire
    form_class = QuestionnaireTranslationForm
    template_name = 'promapp/questionnaire_translation_form.html'
    permission_required = 'promapp.add_questionnaire'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_languages'] = settings.LANGUAGES
        context['current_language'] = self.request.GET.get('language', settings.LANGUAGE_CODE)
        
        # Get the questionnaire instance
        questionnaire = self.get_object()
        
        # Get the original text in the default language
        context['original_name'] = questionnaire.name
        context['original_description'] = questionnaire.description
        
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        current_language = self.request.GET.get('language', settings.LANGUAGE_CODE)
        
        # Get the questionnaire instance
        questionnaire = self.get_object()
        
        # Try to get existing translation
        try:
            translation = questionnaire.translations.get(language_code=current_language)
            # If translation exists, use its values
            kwargs['initial'] = {
                'name': translation.name,
                'description': translation.description
            }
        except questionnaire.translations.model.DoesNotExist:
            # If no translation exists, use empty values
            kwargs['initial'] = {
                'name': '',
                'description': ''
            }
        
        return kwargs

    def form_valid(self, form):
        # Get the current language from the request
        current_language = self.request.GET.get('language', settings.LANGUAGE_CODE)
        questionnaire = self.get_object()
        questionnaire.set_current_language(current_language)
        # Set translated fields
        questionnaire.name = form.cleaned_data['name']
        questionnaire.description = form.cleaned_data['description']
        questionnaire.save()  # This saves the translation for the current language only
        messages.success(self.request, _('Translation saved successfully.'))
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('questionnaire_translation_list')

class QuestionnaireTranslationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    View for listing questionnaires with translation links.
    """
    model = Questionnaire
    template_name = 'promapp/questionnaire_translation_list.html'
    context_object_name = 'questionnaires'
    permission_required = 'promapp.add_questionnaire'

    def get_queryset(self):
        # Get all questionnaires with their translations
        queryset = Questionnaire.objects.all()
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(translations__name__icontains=search)
        
        # Apply language filter if provided
        language_filter = self.request.GET.get('language_filter')
        if language_filter:
            if language_filter.endswith('_translated'):
                # Filter for questionnaires that have translation in the specified language
                lang_code = language_filter.replace('_translated', '')
                queryset = queryset.filter(
                    translations__language_code=lang_code
                ).annotate(
                    has_content=models.Case(
                        models.When(
                            models.Q(translations__name__isnull=False) & 
                            ~models.Q(translations__name='') |
                            models.Q(translations__description__isnull=False) & 
                            ~models.Q(translations__description=''),
                            then=models.Value(True)
                        ),
                        default=models.Value(False),
                        output_field=models.BooleanField()
                    )
                ).filter(has_content=True)
            elif language_filter.endswith('_untranslated'):
                # Filter for questionnaires that don't have translation in the specified language
                lang_code = language_filter.replace('_untranslated', '')
                # Get questionnaires that have meaningful translation content
                translated_name_ids = Questionnaire.objects.filter(
                    translations__language_code=lang_code,
                    translations__name__isnull=False,
                ).exclude(
                    translations__name=''
                ).values_list('id', flat=True)
                
                translated_desc_ids = Questionnaire.objects.filter(
                    translations__language_code=lang_code,
                    translations__description__isnull=False,
                ).exclude(
                    translations__description=''
                ).values_list('id', flat=True)
                
                # Combine both lists to get all translated questionnaires
                all_translated_ids = set(list(translated_name_ids) + list(translated_desc_ids))
                
                queryset = queryset.exclude(id__in=all_translated_ids)
            
        # Prefetch translations for better performance
        queryset = queryset.prefetch_related('translations')
        
        return queryset.distinct().order_by('id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_languages'] = settings.LANGUAGES
        context['current_language'] = self.request.GET.get('language', settings.LANGUAGE_CODE)
        
        # Create form with current values
        form_initial = {
            'search': self.request.GET.get('search', ''),
            'language_filter': self.request.GET.get('language_filter', '')
        }
        context['search_form'] = TranslationSearchForm(initial=form_initial)
        
        # Set up HTMX attributes for both fields
        context['search_form'].fields['search'].widget.attrs['hx-get'] = reverse('questionnaire_translation_list')
        context['search_form'].fields['language_filter'].widget.attrs['hx-get'] = reverse('questionnaire_translation_list')
        
        # Add current filter values for form state preservation
        context['current_search'] = self.request.GET.get('search', '')
        context['current_language_filter'] = self.request.GET.get('language_filter', '')
        
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # Add translation status data for each questionnaire
        questionnaires_with_translation_status = []
        for questionnaire in context['questionnaires']:
            # Get all existing translations for this questionnaire
            existing_translations = set(
                questionnaire.translations.values_list('language_code', flat=True)
            )
            
            translation_status = []
            available_translations = []
            pending_translations = []
            
            for lang_code, lang_name in settings.LANGUAGES:
                has_translation = lang_code in existing_translations
                # Check if translation has content (not just empty strings)
                if has_translation:
                    try:
                        translation = questionnaire.translations.get(language_code=lang_code)
                        has_content = bool(
                            (translation.name and translation.name.strip()) or 
                            (translation.description and translation.description.strip())
                        )
                    except questionnaire.translations.model.DoesNotExist:
                        has_content = False
                else:
                    has_content = False
                    
                status_item = {
                    'language_code': lang_code,
                    'language_name': lang_name,
                    'has_translation': has_translation and has_content,
                    'url': reverse('questionnaire_translation', args=[questionnaire.id]) + f'?language={lang_code}'
                }
                
                translation_status.append(status_item)
                
                if has_translation and has_content:
                    available_translations.append(status_item)
                else:
                    pending_translations.append(status_item)
            
            questionnaires_with_translation_status.append({
                'questionnaire': questionnaire,
                'translation_status': translation_status,
                'available_translations': available_translations,
                'pending_translations': pending_translations,
                'has_available': len(available_translations) > 0,
                'has_pending': len(pending_translations) > 0
            })
        
        context['questionnaires_with_translation_status'] = questionnaires_with_translation_status
        return context
    
    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # If it is an HTMX request, only return the partial template
            self.object_list = self.get_queryset()
            context = self.get_context_data()
            html = render_to_string('promapp/partials/questionnaire_translation_list_table.html', context)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)

class LikertScaleResponseOptionTranslationView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View for managing translations of a LikertScaleResponseOption.
    """
    model = LikertScaleResponseOption
    form_class = LikertScaleResponseOptionTranslationForm
    template_name = 'promapp/likert_scale_response_option_translation_form.html'
    permission_required = 'promapp.add_likertscaleresponseoption'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_languages'] = settings.LANGUAGES
        context['current_language'] = self.request.GET.get('language', settings.LANGUAGE_CODE)
        option = self.get_object()
        context['original_option_text'] = option.option_text
        context['original_option_media'] = option.option_media
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        current_language = self.request.GET.get('language', settings.LANGUAGE_CODE)
        option = self.get_object()
        try:
            translation = option.translations.get(language_code=current_language)
            kwargs['initial'] = {
                'option_text': translation.option_text,
                'option_media': translation.option_media
            }
        except option.translations.model.DoesNotExist:
            kwargs['initial'] = {
                'option_text': '',
                'option_media': None
            }
        return kwargs

    def form_valid(self, form):
        current_language = self.request.GET.get('language', settings.LANGUAGE_CODE)
        option = self.get_object()
        option.set_current_language(current_language)
        option.option_text = form.cleaned_data['option_text']
        option.option_media = form.cleaned_data['option_media']
        option.save()
        messages.success(self.request, _('Translation saved successfully.'))
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('likert_scale_list')
    
class LikertScaleResponseOptionTranslationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    View for listing LikertScaleResponseOptions with translation links.
    """
    model = LikertScaleResponseOption
    template_name = 'promapp/likert_scale_response_option_translation_list.html'
    context_object_name = 'options'
    permission_required = 'promapp.add_likertscaleresponseoption'

    def get_queryset(self):
        queryset = LikertScaleResponseOption.objects.language(settings.LANGUAGE_CODE).distinct('id').order_by('id', 'translations__option_text')
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(translations__option_text__icontains=search)
        
        # Apply language filter if provided
        language_filter = self.request.GET.get('language_filter')
        if language_filter:
            if language_filter.endswith('_translated'):
                # Filter for options that have translation in the specified language
                lang_code = language_filter.replace('_translated', '')
                queryset = LikertScaleResponseOption.objects.filter(
                    translations__language_code=lang_code
                ).annotate(
                    has_content=models.Case(
                        models.When(
                            models.Q(translations__option_text__isnull=False) & 
                            ~models.Q(translations__option_text=''),
                            then=models.Value(True)
                        ),
                        default=models.Value(False),
                        output_field=models.BooleanField()
                    )
                ).filter(has_content=True).distinct('id').order_by('id')
            elif language_filter.endswith('_untranslated'):
                # Filter for options that don't have translation in the specified language
                lang_code = language_filter.replace('_untranslated', '')
                # Get options that have meaningful translation content
                translated_option_ids = LikertScaleResponseOption.objects.filter(
                    translations__language_code=lang_code,
                    translations__option_text__isnull=False,
                ).exclude(
                    translations__option_text=''
                ).values_list('id', flat=True)
                
                queryset = LikertScaleResponseOption.objects.exclude(
                    id__in=translated_option_ids
                ).distinct('id').order_by('id')
            
        return queryset

    def get_context_data(self, **kwargs):   
        context = super().get_context_data(**kwargs)
        context['available_languages'] = settings.LANGUAGES
        context['current_language'] = self.request.GET.get('language', settings.LANGUAGE_CODE)
        
        # Create form with current values
        form_initial = {
            'search': self.request.GET.get('search', ''),
            'language_filter': self.request.GET.get('language_filter', '')
        }
        context['search_form'] = TranslationSearchForm(initial=form_initial)
        
        # Set up HTMX attributes for both fields
        context['search_form'].fields['search'].widget.attrs['hx-get'] = reverse('likert_scale_response_option_translation_list')
        context['search_form'].fields['language_filter'].widget.attrs['hx-get'] = reverse('likert_scale_response_option_translation_list')
        
        # Add current filter values for form state preservation
        context['current_search'] = self.request.GET.get('search', '')
        context['current_language_filter'] = self.request.GET.get('language_filter', '')
        
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # Add translation status data for each option
        options_with_translation_status = []
        for option in context['options']:
            # Get all existing translations for this option
            existing_translations = set(
                option.translations.values_list('language_code', flat=True)
            )
            
            translation_status = []
            for lang_code, lang_name in settings.LANGUAGES:
                has_translation = lang_code in existing_translations
                # Check if translation has content (not just empty strings)
                if has_translation:
                    try:
                        translation = option.translations.get(language_code=lang_code)
                        has_content = bool(translation.option_text and translation.option_text.strip())
                    except option.translations.model.DoesNotExist:
                        has_content = False
                else:
                    has_content = False
                    
                translation_status.append({
                    'language_code': lang_code,
                    'language_name': lang_name,
                    'has_translation': has_translation and has_content,
                    'url': reverse('likert_scale_response_option_translation', args=[option.id]) + f'?language={lang_code}'
                })
            
            options_with_translation_status.append({
                'option': option,
                'translation_status': translation_status
            })
        
        context['options_with_translation_status'] = options_with_translation_status
        return context

    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # If it is an HTMX request, only return the table part
            self.object_list = self.get_queryset()
            context = self.get_context_data()
            html = render_to_string('promapp/partials/likert_scale_response_option_translation_list_table.html', context)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)

class RangeScaleTranslationView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View for managing translations of a RangeScale.
    """
    model = RangeScale
    form_class = RangeScaleTranslationForm
    template_name = 'promapp/range_scale_translation_form.html'
    permission_required = 'promapp.add_rangescale'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_languages'] = settings.LANGUAGES
        context['current_language'] = self.request.GET.get('language', settings.LANGUAGE_CODE)
        scale = self.get_object()
        context['original_min_value_text'] = scale.min_value_text
        context['original_max_value_text'] = scale.max_value_text
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        current_language = self.request.GET.get('language', settings.LANGUAGE_CODE)
        scale = self.get_object()
        try:
            translation = scale.translations.get(language_code=current_language)
            kwargs['initial'] = {
                'min_value_text': translation.min_value_text,
                'max_value_text': translation.max_value_text
            }
        except scale.translations.model.DoesNotExist:
            kwargs['initial'] = {
                'min_value_text': '',
                'max_value_text': ''
            }
        return kwargs

    def form_valid(self, form):
        current_language = self.request.GET.get('language', settings.LANGUAGE_CODE)
        scale = self.get_object()
        scale.set_current_language(current_language)
        scale.min_value_text = form.cleaned_data['min_value_text']
        scale.max_value_text = form.cleaned_data['max_value_text']
        scale.save()
        messages.success(self.request, _('Translation saved successfully.'))
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('range_scale_list')

class RangeScaleTranslationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    View for listing range scales with translation links.
    """
    model = RangeScale
    template_name = 'promapp/range_scale_translation_list.html'
    context_object_name = 'range_scales'
    permission_required = 'promapp.add_rangescale'

    def get_queryset(self):
        queryset = RangeScale.objects.language(settings.LANGUAGE_CODE).distinct('id').order_by('id', 'translations__min_value_text')
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                models.Q(translations__min_value_text__icontains=search) |
                models.Q(translations__max_value_text__icontains=search)
            )
        
        # Apply language filter if provided
        language_filter = self.request.GET.get('language_filter')
        if language_filter:
            if language_filter.endswith('_translated'):
                # Filter for range scales that have translation in the specified language
                lang_code = language_filter.replace('_translated', '')
                queryset = RangeScale.objects.filter(
                    translations__language_code=lang_code
                ).annotate(
                    has_content=models.Case(
                        models.When(
                            models.Q(translations__min_value_text__isnull=False) & 
                            ~models.Q(translations__min_value_text='') |
                            models.Q(translations__max_value_text__isnull=False) & 
                            ~models.Q(translations__max_value_text=''),
                            then=models.Value(True)
                        ),
                        default=models.Value(False),
                        output_field=models.BooleanField()
                    )
                ).filter(has_content=True).distinct('id').order_by('id')
            elif language_filter.endswith('_untranslated'):
                # Filter for range scales that don't have translation in the specified language
                lang_code = language_filter.replace('_untranslated', '')
                # Get range scales that have meaningful translation content
                translated_min_ids = RangeScale.objects.filter(
                    translations__language_code=lang_code,
                    translations__min_value_text__isnull=False,
                ).exclude(
                    translations__min_value_text=''
                ).values_list('id', flat=True)
                
                translated_max_ids = RangeScale.objects.filter(
                    translations__language_code=lang_code,
                    translations__max_value_text__isnull=False,
                ).exclude(
                    translations__max_value_text=''
                ).values_list('id', flat=True)
                
                # Combine both lists to get all translated range scales
                all_translated_ids = set(list(translated_min_ids) + list(translated_max_ids))
                
                queryset = RangeScale.objects.exclude(
                    id__in=all_translated_ids
                ).distinct('id').order_by('id')
            
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_languages'] = settings.LANGUAGES
        context['current_language'] = self.request.GET.get('language', settings.LANGUAGE_CODE)
        
        # Create form with current values
        form_initial = {
            'search': self.request.GET.get('search', ''),
            'language_filter': self.request.GET.get('language_filter', '')
        }
        context['search_form'] = TranslationSearchForm(initial=form_initial)
        
        # Set up HTMX attributes for both fields
        context['search_form'].fields['search'].widget.attrs['hx-get'] = reverse('range_scale_translation_list')
        context['search_form'].fields['language_filter'].widget.attrs['hx-get'] = reverse('range_scale_translation_list')
        
        # Add current filter values for form state preservation
        context['current_search'] = self.request.GET.get('search', '')
        context['current_language_filter'] = self.request.GET.get('language_filter', '')
        
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # Add translation status data for each range scale
        range_scales_with_translation_status = []
        for scale in context['range_scales']:
            # Get all existing translations for this scale
            existing_translations = set(
                scale.translations.values_list('language_code', flat=True)
            )
            
            translation_status = []
            for lang_code, lang_name in settings.LANGUAGES:
                has_translation = lang_code in existing_translations
                # Check if translation has content (not just empty strings)
                if has_translation:
                    try:
                        translation = scale.translations.get(language_code=lang_code)
                        has_content = bool(
                            (translation.min_value_text and translation.min_value_text.strip()) or 
                            (translation.max_value_text and translation.max_value_text.strip())
                        )
                    except scale.translations.model.DoesNotExist:
                        has_content = False
                else:
                    has_content = False
                    
                translation_status.append({
                    'language_code': lang_code,
                    'language_name': lang_name,
                    'has_translation': has_translation and has_content,
                    'url': reverse('range_scale_translate', args=[scale.id]) + f'?language={lang_code}'
                })
            
            range_scales_with_translation_status.append({
                'scale': scale,
                'translation_status': translation_status
            })
        
        context['range_scales_with_translation_status'] = range_scales_with_translation_status
        return context

    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # If it is an HTMX request, only return the table part
            self.object_list = self.get_queryset()
            context = self.get_context_data()
            html = render_to_string('promapp/partials/range_scale_translation_list_table.html', context)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)

def switch_language(request):
    """
    View to switch the current language for translation.
    """
    language = request.GET.get('language')
    if language and language in [lang[0] for lang in settings.LANGUAGES]:
        # Validate the next URL to prevent open redirect attacks
        next_url = request.GET.get('next', '/')
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None, require_https=request.is_secure()):
            next_url = '/'  # Fallback to safe default
        
        if '?' in next_url:
            next_url += '&language=' + language
        else:
            next_url += '?language=' + language
        return redirect(next_url)
    
    # Validate the next URL for the fallback redirect as well
    next_url = request.GET.get('next', '/')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None, require_https=request.is_secure()):
        next_url = '/'  # Fallback to safe default
    return redirect(next_url)

class TranslationsDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    View for the translations dashboard.
    """
    template_name = 'promapp/translations_dashboard.html'
    permission_required = 'promapp.add_questionnaire'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_languages'] = settings.LANGUAGES
        context['current_language'] = self.request.GET.get('language', settings.LANGUAGE_CODE)
        return context

def search_construct_scales(request):
    """Search construct scales and return matching results as JSON."""
    search_query = request.GET.get('q', '')
    scale_id = request.GET.get('id', '')
    
    # If a specific ID is requested, return that scale
    if scale_id:
        try:
            scale = ConstructScale.objects.get(id=scale_id)
            return JsonResponse({'results': [{'id': scale.id, 'text': scale.name}]})
        except ConstructScale.DoesNotExist:
            return JsonResponse({'results': []})
    
    # Otherwise, search by query
    if not search_query:
        return JsonResponse({'results': []})
    
    scales = ConstructScale.objects.filter(name__icontains=search_query).order_by('name')[:10]
    results = [{'id': scale.id, 'text': scale.name} for scale in scales]
    return JsonResponse({'results': results})

class ConstructEquationView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View for managing the equation of a construct scale.
    """
    model = ConstructScale
    form_class = ConstructEquationForm
    template_name = 'promapp/construct_equation_form.html'
    permission_required = 'promapp.change_constructscale'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        construct_scale = self.get_object()
        
        # Get all items associated with this construct scale
        items = Item.objects.filter(construct_scale=construct_scale).order_by('id')
        
        # Get valid items with their generated question numbers
        valid_items_with_numbers = construct_scale.get_valid_items_with_numbers()
        
        # Separate valid and invalid items
        valid_items = [item_data['item'] for item_data in valid_items_with_numbers]
        invalid_items = [item for item in items if item.response_type not in ['Number', 'Likert', 'Range']]
        
        # Add question numbers to the context for the template
        context['valid_items_with_numbers'] = valid_items_with_numbers
        context['valid_items'] = valid_items
        context['invalid_items'] = invalid_items
        return context

    def form_valid(self, form):
        try:
            # Get the cleaned data
            cleaned_data = form.cleaned_data
            construct_scale = form.save(commit=False)
            
            # Set the scale equation
            construct_scale.scale_equation = cleaned_data.get('scale_equation')
            
            # Validate the equation
            try:
                construct_scale.validate_scale_equation()
                construct_scale.save()
                messages.success(self.request, _('Equation saved successfully.'))
                return redirect('construct_scale_list')
            except ValidationError as e:
                # Format the validation error message
                error_message = str(e)
                if error_message.startswith('__all__:'):
                    error_message = error_message.replace('__all__:', '').strip()
                form.add_error('scale_equation', error_message)
                messages.error(self.request, error_message)
                return self.form_invalid(form)
                
        except Exception as e:
            # Log detailed error but show generic message
            logger = logging.getLogger("promapp.construct_equations")
            logger.error(f"Unexpected error saving construct equation for scale {self.get_object().id}: {str(e)}")
            messages.error(self.request, "An unexpected error occurred while saving the equation. Please try again.")
            return self.form_invalid(form)

    def form_invalid(self, form):
        # Format and display form errors
        for field, errors in form.errors.items():
            for error in errors:
                # Remove any field prefix from the error message
                error_message = error
                if error_message.startswith(field + ':'):
                    error_message = error_message.replace(field + ':', '').strip()
                messages.error(self.request, error_message)
        return super().form_invalid(form)

class ConstructScaleUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View for updating a construct scale.
    """
    model = ConstructScale
    form_class = ConstructScaleForm
    template_name = 'promapp/construct_scale_form.html'
    permission_required = 'promapp.change_constructscale'

    def get_success_url(self):
        return reverse('construct_scale_list')

    def form_valid(self, form):
        messages.success(self.request, _('Construct scale updated successfully.'))
        return super().form_valid(form)

class ConstructScaleDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    View for deleting a construct scale.
    """
    model = ConstructScale
    permission_required = 'promapp.delete_constructscale'

    def get_success_url(self):
        return reverse('construct_scale_list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, _('Construct scale deleted successfully.'))
        return super().delete(request, *args, **kwargs)

def validate_equation(request):
    """
    HTMX endpoint to validate an equation in real-time.
    """
    equation = request.GET.get('value', '')
    scale_id = request.GET.get('scale_id')
    
    try:
        # Normalize line endings and whitespace
        equation = equation.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')
        equation = ' '.join(equation.split())  # Normalize whitespace
        
        # If we have a scale_id, use the actual scale for validation
        # Otherwise create a temporary one (which won't have items to validate against)
        if scale_id:
            try:
                actual_scale = ConstructScale.objects.get(id=scale_id)
                # Temporarily set the equation on the actual scale for validation
                # Save the original equation to restore it after validation
                original_equation = actual_scale.scale_equation
                actual_scale.scale_equation = equation
                actual_scale.validate_scale_equation()
                # Restore original equation (we're not saving, just validating)
                actual_scale.scale_equation = original_equation
            except ConstructScale.DoesNotExist:
                # If scale doesn't exist, just validate syntax without item references
                temp_scale = ConstructScale(scale_equation=equation)
                temp_scale.validate_scale_equation()
        else:
            # No scale_id provided, create temporary scale for basic validation
            temp_scale = ConstructScale(scale_equation=equation)
            temp_scale.validate_scale_equation()
        return HttpResponse('<div class="text-green-600">✓ Valid equation</div>')
    except ValidationError as e:
        # Log the detailed error for debugging but return sanitized message
        logger = logging.getLogger("promapp.equations")
        logger.error(f"Equation validation error for equation '{equation}': {str(e)}")
        return HttpResponse(f'<div class="text-red-600">✗ {escape(str(e))}</div>')
    except Exception as e:
        # Log unexpected errors but return generic message
        logger = logging.getLogger("promapp.equations")
        logger.error(f"Unexpected error validating equation '{equation}': {str(e)}")
        return HttpResponse('<div class="text-red-600">✗ Invalid equation format</div>')

def add_to_equation(request):
    """
    HTMX endpoint to add a question reference to the equation.
    """
    question = request.GET.get('question', '')
    if not question:
        return HttpResponse('')
    return HttpResponse(html.escape(question))

class CompositeConstructScaleScoringListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    View for listing composite construct scale scoring configurations.
    """
    model = CompositeConstructScaleScoring
    template_name = 'promapp/composite_construct_scale_scoring_list.html'
    context_object_name = 'composite_scales'
    permission_required = 'promapp.view_compositeconstructscalescoring'
    paginate_by = 25  # Show 25 items per page
    
    def get_queryset(self):
        queryset = CompositeConstructScaleScoring.objects.all().prefetch_related('construct_scales')
        
        # Apply search filter if provided
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(composite_construct_scale_name__icontains=search)
        
        # Apply scoring type filter if provided
        scoring_type = self.request.GET.get('scoring_type')
        if scoring_type and scoring_type != 'all':
            queryset = queryset.filter(scoring_type=scoring_type)
        
        # Apply construct count filter if provided
        construct_count = self.request.GET.get('construct_count')
        if construct_count and construct_count != 'all':
            queryset = queryset.annotate(
                num_constructs=models.Count('construct_scales', distinct=True)
            )
            if construct_count == 'few':
                # 2-3 constructs
                queryset = queryset.filter(num_constructs__gte=2, num_constructs__lte=3)
            elif construct_count == 'medium':
                # 4-5 constructs
                queryset = queryset.filter(num_constructs__gte=4, num_constructs__lte=5)
            elif construct_count == 'many':
                # 6+ constructs
                queryset = queryset.filter(num_constructs__gte=6)
            
        return queryset.order_by('composite_construct_scale_name')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        context['is_htmx'] = bool(self.request.META.get('HTTP_HX_REQUEST'))
        
        # Get scoring type choices for filter
        from .models import ScoringTypeChoices
        scoring_type_choices = [{'value': choice[0], 'label': choice[1]} for choice in ScoringTypeChoices.choices]
        
        # Create filters for the search component
        context['composite_scale_filters'] = [
            {
                'type': 'select',
                'name': 'scoring_type',
                'label': 'Scoring type',
                'selected': self.request.GET.get('scoring_type', 'all'),
                'options': scoring_type_choices,
                'trigger': 'hx-trigger="change"'
            },
            {
                'type': 'select',
                'name': 'construct_count',
                'label': 'Number of constructs',
                'selected': self.request.GET.get('construct_count', 'all'),
                'options': [
                    {'value': 'few', 'label': 'Few (2-3)'},
                    {'value': 'medium', 'label': 'Medium (4-5)'},
                    {'value': 'many', 'label': 'Many (6+)'}
                ],
                'trigger': 'hx-trigger="change"'
            }
        ]
        
        return context
    
    def get(self, request, *args, **kwargs):
        # Check if this is an HTMX request
        if request.META.get('HTTP_HX_REQUEST'):
            # For HTMX requests, let Django handle pagination normally
            # but just return the partial template
            response = super().get(request, *args, **kwargs)
            
            # If the superclass call resulted in a successful response
            if hasattr(response, 'context_data'):
                context = response.context_data
            else:
                # Fallback to getting context manually
                context = self.get_context_data()
            
            # Render only the table part for HTMX
            html = render_to_string('promapp/partials/composite_construct_scale_scoring_list_table.html', context, request=request)
            return HttpResponse(html)
        
        # Otherwise, return the full page as usual
        return super().get(request, *args, **kwargs)


class CompositeConstructScaleScoringCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    View for creating a new composite construct scale scoring configuration.
    """
    model = CompositeConstructScaleScoring
    form_class = CompositeConstructScaleScoringForm
    template_name = 'promapp/composite_construct_scale_scoring_form.html'
    permission_required = 'promapp.add_compositeconstructscalescoring'

    def get_success_url(self):
        return reverse('composite_construct_scale_scoring_list')

    def form_valid(self, form):
        messages.success(self.request, _('Composite construct scale scoring created successfully.'))
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, _('Please correct the errors below.'))
        return super().form_invalid(form)


class CompositeConstructScaleScoringUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View for updating a composite construct scale scoring configuration.
    """
    model = CompositeConstructScaleScoring
    form_class = CompositeConstructScaleScoringForm
    template_name = 'promapp/composite_construct_scale_scoring_form.html'
    permission_required = 'promapp.change_compositeconstructscalescoring'

    def get_success_url(self):
        return reverse('composite_construct_scale_scoring_list')

    def form_valid(self, form):
        messages.success(self.request, _('Composite construct scale scoring updated successfully.'))
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, _('Please correct the errors below.'))
        return super().form_invalid(form)


class CompositeConstructScaleScoringDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    View for deleting a composite construct scale scoring configuration.
    """
    model = CompositeConstructScaleScoring
    permission_required = 'promapp.delete_compositeconstructscalescoring'

    def get_success_url(self):
        return reverse('composite_construct_scale_scoring_list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, _('Composite construct scale scoring deleted successfully.'))
        return super().delete(request, *args, **kwargs)


class QuestionnaireGuidanceView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    View for the redesigned questionnaire guidance page with step-by-step workflow.
    This page provides a clear 4-step process for creating questionnaires
    with visual workflow and direct links to creation forms.
    """
    template_name = 'promapp/questionnaire_guidance.html'
    permission_required = 'promapp.add_questionnaire'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'Questionnaire Creation Guide'
        return context


def get_questionnaire_count():
    """
    Get the total count of questionnaires in the system.
    Returns an integer count.
    """
    return Questionnaire.objects.count()


def get_item_count():
    """
    Get the total count of items (questions) in the system.
    Returns an integer count.
    """
    return Item.objects.count()


def get_questionnaire_submission_count():
    """
    Get the total count of questionnaire submissions (responses) in the system.
    Returns an integer count.
    """
    return QuestionnaireSubmission.objects.count()


class StaffQuestionnaireResponseView(LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin, DetailView):
    """
    View for staff to submit questionnaire responses on behalf of patients.
    Displays all questions on a single page with patient selection and custom submission date.
    Only accessible to users with a Provider profile AND required permissions.
    """
    model = Questionnaire
    template_name = 'promapp/staff_questionnaire_response.html'
    context_object_name = 'questionnaire'
    permission_required = [
        'promapp.view_questionnaire',
        'promapp.add_questionnaireitemresponse',
        'promapp.add_questionnairesubmission',
        'patientapp.view_patient'
    ]
    
    def test_func(self):
        """
        Check if user has a Provider profile.
        This is checked AFTER permissions are verified.
        """
        from providerapp.models import Provider
        return hasattr(self.request.user, 'provider') and Provider.objects.filter(user=self.request.user).exists()

    def get_translated_items(self, questionnaire):
        """Helper method to get questionnaire items with properly translated Likert options"""
        current_language = get_language()
        questionnaire_items = QuestionnaireItem.objects.filter(
            questionnaire=questionnaire
        ).select_related(
            'item',
            'item__likert_response',
            'item__range_response'
        ).prefetch_related(
            'item__likert_response__likertscaleresponseoption_set'
        ).order_by('question_number')
        
        # Prepare questionnaire items with translated Likert options
        items_with_translations = []
        for qi in questionnaire_items:
            item_data = {
                'questionnaire_item': qi,
                'translated_options': [],
                'translated_range_scale': None
            }
            
            # If this is a Likert type question, get translated options
            if qi.item.response_type == 'Likert' and qi.item.likert_response:
                try:
                    # Try to get options in current language
                    options = qi.item.likert_response.likertscaleresponseoption_set.language(current_language).order_by('option_order')
                except:
                    # Fallback to English or any available language
                    try:
                        options = qi.item.likert_response.likertscaleresponseoption_set.language('en-gb').order_by('option_order')
                    except:
                        # Last fallback to all options
                        options = qi.item.likert_response.likertscaleresponseoption_set.all().order_by('option_order')
                
                item_data['translated_options'] = options
            
            # If this is a Range type question, get translated range scale
            elif qi.item.response_type == 'Range' and qi.item.range_response:
                try:
                    # Try to get range scale in current language
                    range_scale = qi.item.range_response
                    range_scale.set_current_language(current_language)
                    item_data['translated_range_scale'] = range_scale
                except:
                    # Fallback to English or default language
                    try:
                        range_scale = qi.item.range_response
                        range_scale.set_current_language('en-gb')
                        item_data['translated_range_scale'] = range_scale
                    except:
                        # Last fallback to original range scale
                        item_data['translated_range_scale'] = qi.item.range_response
            
            items_with_translations.append(item_data)
        
        return questionnaire_items, items_with_translations

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questionnaire = self.object
        
        # Get all items for this questionnaire with translations
        questionnaire_items, items_with_translations = self.get_translated_items(questionnaire)
        
        # Create the form with the questionnaire items
        # Set initial submission_date to current time
        initial_data = {'submission_date': timezone.now().strftime('%Y-%m-%dT%H:%M')}
        form = StaffQuestionnaireResponseForm(
            questionnaire_items=questionnaire_items,
            initial=initial_data
        )
        
        context.update({
            'form': form,
            'questionnaire_items': questionnaire_items,
            'items_with_translations': items_with_translations,
        })
        return context

    def post(self, request, *args, **kwargs):
        # Set self.object for DetailView compatibility
        self.object = self.get_object()
        questionnaire = self.object
        
        # Check if user has permission to add responses
        if not request.user.has_perm('promapp.add_questionnaireitemresponse'):
            messages.error(request, _('You do not have permission to submit responses.'))
            return self.render_to_response(self.get_context_data())
        
        # Get all items for this questionnaire with translations
        questionnaire_items, items_with_translations = self.get_translated_items(questionnaire)
        
        # Create the form with the questionnaire items and file uploads
        form = StaffQuestionnaireResponseForm(
            request.POST, 
            request.FILES, 
            questionnaire_items=questionnaire_items
        )
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    # Get the patient from the form
                    patient = form.cleaned_data['patient']
                    submission_date = form.cleaned_data['submission_date']
                    
                    # Check institution access - staff can only submit for patients in their institution
                    from patientapp.utils import check_patient_access
                    if not check_patient_access(request.user, patient):
                        messages.error(request, _('You do not have permission to submit questionnaires for this patient.'))
                        context = self.get_context_data(form=form)
                        context['items_with_translations'] = items_with_translations
                        return self.render_to_response(context)
                    
                    # Get or create PatientQuestionnaire
                    patient_questionnaire, created = PatientQuestionnaire.objects.get_or_create(
                        patient=patient,
                        questionnaire=questionnaire,
                        defaults={'display_questionnaire': True}
                    )
                    
                    # Create a new submission record
                    submission = QuestionnaireSubmission.objects.create(
                        patient=patient,
                        patient_questionnaire=patient_questionnaire,
                        user_submitting_questionnaire=request.user,
                        submission_date=submission_date
                    )
                    
                    # Create responses for all items, including unanswered ones
                    for qi in questionnaire_items:
                        response_value = form.cleaned_data.get(f'response_{qi.id}')
                        response_media = None
                        
                        # Handle media files for Media response type
                        if qi.item.response_type == 'Media':
                            # Check if there's a media file for this question
                            media_field_name = f'response_media_{qi.id}'
                            if media_field_name in request.FILES:
                                response_media = request.FILES[media_field_name]
                        
                        # Create record for every question, even if unanswered
                        QuestionnaireItemResponse.objects.create(
                            questionnaire_submission=submission,
                            questionnaire_item=qi,
                            response_value=str(response_value) if response_value is not None else None,
                            response_media=response_media
                        )
                    
                    # Construct scores will be calculated automatically by the post_save signal
                    # on QuestionnaireItemResponse (see models.py trigger_score_calculation_on_response)
                    
                    messages.success(request, _('Questionnaire responses have been saved successfully for patient: %(patient)s') % {'patient': patient.name})
                    return redirect('questionnaire_list')
            except Exception as e:
                # Log detailed error but show generic message
                logger = logging.getLogger("promapp.staff_questionnaire_responses")
                logger.error(f"Error saving staff questionnaire responses for patient {form.cleaned_data.get('patient', 'unknown')}: {str(e)}")
                messages.error(request, _('An error occurred while saving the responses. Please try again or contact support if the problem persists.'))
                # Pass items_with_translations in the context for error cases
                context = self.get_context_data(form=form)
                context['items_with_translations'] = items_with_translations
                return self.render_to_response(context)
        else:
            messages.error(request, _('There was an error with the form. Please check all fields and try again.'))
            # Pass items_with_translations in the context for error cases
            context = self.get_context_data(form=form)
            context['items_with_translations'] = items_with_translations
            context['form'] = form  # Pass the form with errors
            return self.render_to_response(context)
