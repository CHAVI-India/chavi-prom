# Questionnaire Redirect Issue - Fix Documentation

## Problem Description

In production with Gunicorn (5 workers), after a patient completes a questionnaire, they were being redirected back to the first questionnaire instead of proceeding to the next one in sequence.

### Log Analysis

From the production logs:
```
[2025-11-17 12:38:51] POST /bn/promapp/questionnaire/76c65a3a.../response/ HTTP/1.0" 302
[2025-11-17 12:38:52] GET /bn/promapp/questionnaire/2b06f325.../response/ HTTP/1.0" 200
[2025-11-17 12:38:53] POST /bn/promapp/questionnaire/76c65a3a.../response/ HTTP/1.0" 200  <- PROBLEM
[2025-11-17 12:38:54] GET /bn/promapp/questionnaire/76c65a3a.../response/ HTTP/1.0" 200
```

The patient was redirected to the next questionnaire (2b06f325...) but then immediately redirected back to the first one (76c65a3a...).

## Root Cause

The issue was in the `QuestionnaireResponseView.dispatch()` method. When a patient was redirected to the next questionnaire after completing one:

1. The `dispatch()` method would call `check_interval()` on the target questionnaire
2. If that questionnaire had been recently completed (within its answer interval), `check_interval()` would return `False`
3. The view would then **render the form** for that questionnaire instead of finding the next available one
4. This caused the patient to be stuck or redirected back to a previous questionnaire

### Why This Happened in Production

The multi-worker Gunicorn environment made this more apparent because:
- Different workers handle different requests
- Transaction isolation could cause slight delays in seeing newly created submissions
- The redirect logic didn't account for questionnaires that couldn't be answered yet

## Solution Implemented

### 1. Enhanced `dispatch()` Method

Modified the dispatch method to handle the case where a questionnaire cannot be answered:

```python
def dispatch(self, request, *args, **kwargs):
    self.object = self.get_object()
    can_answer, next_available = self.check_interval(self.object)
    
    if not can_answer:
        # NEW: Try to find the next available questionnaire
        next_questionnaire = self.get_next_available_questionnaire(self.object)
        
        if next_questionnaire and next_questionnaire.id != self.object.id:
            # Redirect to the next available questionnaire
            return redirect('questionnaire_response', pk=next_questionnaire.id)
        else:
            # No more questionnaires available
            return redirect('my_questionnaire_list')
    
    return super().dispatch(request, *args, **kwargs)
```

**Key Changes:**
- When a questionnaire cannot be answered, instead of showing an error, we now look for the next available questionnaire
- If found, we redirect to it
- If no more questionnaires are available, we redirect to the questionnaire list

### 2. Simplified `get_next_available_questionnaire()` Method

Refactored to use the existing `check_interval()` method for consistency:

```python
def get_next_available_questionnaire(self, current_patient_questionnaire):
    # Get all questionnaires after the current one
    patient_questionnaires = PatientQuestionnaire.objects.filter(
        patient=self.request.user.patient,
        display_questionnaire=True
    ).select_related('questionnaire').order_by('questionnaire__questionnaire_order')
    
    current_order = current_patient_questionnaire.questionnaire.questionnaire_order
    next_questionnaires = patient_questionnaires.filter(
        questionnaire__questionnaire_order__gt=current_order
    )
    
    # Check each subsequent questionnaire
    for pq in next_questionnaires:
        can_answer, _ = self.check_interval(pq)
        if can_answer:
            return pq
    
    return None
```

**Key Changes:**
- Removed duplicate logic for checking submission intervals
- Now uses `check_interval()` for consistency
- Cleaner and more maintainable code

### 3. Added Comprehensive Logging

Added logging throughout the questionnaire response flow:

```python
logger.info(f"Dispatch for questionnaire {self.object.questionnaire.id} (order: {self.object.questionnaire.questionnaire_order}), can_answer: {can_answer}")
logger.info(f"POST request for questionnaire {patient_questionnaire.questionnaire.id} by patient {request.user.patient.id}")
logger.info(f"Submission {submission.id} created successfully for questionnaire {patient_questionnaire.questionnaire.id}")
logger.info(f"Redirecting to next questionnaire {next_questionnaire.questionnaire.id} (order: {next_questionnaire.questionnaire.questionnaire_order})")
```

**Benefits:**
- Easier debugging in production
- Track questionnaire flow through the logs
- Identify any remaining edge cases

## Expected Behavior After Fix

1. Patient completes questionnaire A
2. POST request creates submission and redirects to questionnaire B
3. GET request for questionnaire B:
   - If B can be answered → show form for B
   - If B cannot be answered → find next available questionnaire C
   - If no more questionnaires → redirect to questionnaire list
4. Patient continues through available questionnaires in sequence

## Testing Recommendations

### Test Case 1: Normal Flow
1. Assign multiple questionnaires to a patient in sequence
2. Complete first questionnaire
3. Verify redirect to second questionnaire
4. Complete second questionnaire
5. Verify redirect to third questionnaire or completion message

### Test Case 2: Questionnaire with Interval
1. Assign questionnaires with answer intervals (e.g., 1 hour)
2. Complete first questionnaire
3. Verify redirect skips questionnaires that cannot be answered yet
4. Verify redirect goes to the first available questionnaire

### Test Case 3: All Questionnaires Completed
1. Complete all assigned questionnaires
2. Verify redirect to questionnaire list
3. Verify appropriate completion message

### Test Case 4: Multi-Worker Environment
1. Test in production with Gunicorn (5 workers)
2. Verify no race conditions or redirect loops
3. Check logs for proper flow tracking

## Files Modified

- `/home/santam/chavi-prom/promapp/views.py`
  - `QuestionnaireResponseView.dispatch()` - Enhanced redirect logic
  - `QuestionnaireResponseView.get_next_available_questionnaire()` - Simplified implementation
  - `QuestionnaireResponseView.post()` - Added logging

## Deployment Notes

1. No database migrations required
2. No settings changes required
3. Restart Gunicorn after deploying:
   ```bash
   sudo systemctl restart gunicorn
   ```
4. Monitor logs for the new logging statements:
   ```bash
   tail -f logs/gunicorn-access.log
   tail -f logs/gunicorn-error.log
   ```

## Monitoring

After deployment, monitor for:
- Successful questionnaire completions
- Proper redirect flow in logs
- No redirect loops or errors
- Patient feedback on questionnaire flow

Look for log entries like:
```
INFO promapp.questionnaire_responses Dispatch for questionnaire <id> (order: <n>), can_answer: True
INFO promapp.questionnaire_responses POST request for questionnaire <id> by patient <id>
INFO promapp.questionnaire_responses Submission <id> created successfully
INFO promapp.questionnaire_responses Redirecting to next questionnaire <id> (order: <n>)
```
