from django.urls import path
from risk import views
from risk.views import security, strategy, alternatives, compare, stress, exports, explain

app_name = 'risk'

urlpatterns = [
    # M6-Spec-1 routes
    path('dashboard/',                     views.dashboard,         name='dashboard'),
    path('account/<str:account_id>/',      views.account_summary,   name='account_summary'),
    path('owner/<str:owner>/',             views.owner_summary,     name='owner_summary'),
    path('heatmap/',                       views.risk_heatmap,      name='heatmap'),
    # M6-Spec-2 routes
    path('security/<str:symbol>/',         security.security_detail, name='security_detail'),
    path('security/<str:symbol>/export/<str:fmt>/',
                                         security.security_export,        name='security_export'),
    path('strategy/<int:rec_id>/',         strategy.strategy_report, name='strategy_report'),
    path('strategy/<int:rec_id>/acknowledge/',
                                         strategy.strategy_acknowledge, name='strategy_acknowledge'),
    path('strategy/<int:rec_id>/mark_actioned/',
                                         strategy.strategy_mark_actioned, name='strategy_mark_actioned'),
    path('alternatives/',                  alternatives.alternatives_list, name='alternatives_list'),
    path('alternatives/create/',           alternatives.alternatives_create,
                                         name='alternatives_create'),
    path('alternatives/<str:account_id>/', alternatives.alternatives_account,
                                         name='alternatives_account'),
    path('alternatives/<int:portfolio_id>/edit/',
                                         alternatives.alternatives_edit,  name='alternatives_edit'),
    path('compare/',                       compare.portfolio_compare, name='portfolio_compare'),
    path('stress/<int:run_id>/',           stress.stress_results,   name='stress_results'),
    path('export/strategy/<int:rec_id>/<str:fmt>/',
                                         exports.strategy_export,         name='strategy_export'),
    # M8-Spec-1: Explainability
    path('explain/<int:urgency_score_id>/',
                                         explain.explain_score,           name='explain_score'),
]
