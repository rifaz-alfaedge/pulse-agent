app_name = "pulse_agent"
app_title = "Pulse Agent"
app_publisher = "AlfaEdge"
app_description = "Push agent for alfaEdge Pulse Host Health monitoring"
app_email = "rifazmohammed@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "pulse_agent",
# 		"logo": "/assets/pulse_agent/logo.png",
# 		"title": "Pulse Agent",
# 		"route": "/pulse_agent",
# 		"has_permission": "pulse_agent.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/pulse_agent/css/pulse_agent.css"
# app_include_js = "/assets/pulse_agent/js/pulse_agent.js"

# include js, css files in header of web template
# web_include_css = "/assets/pulse_agent/css/pulse_agent.css"
# web_include_js = "/assets/pulse_agent/js/pulse_agent.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "pulse_agent/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "pulse_agent/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "pulse_agent.utils.jinja_methods",
# 	"filters": "pulse_agent.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "pulse_agent.install.before_install"
# after_install = "pulse_agent.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "pulse_agent.uninstall.before_uninstall"
# after_uninstall = "pulse_agent.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "pulse_agent.utils.before_app_install"
# after_app_install = "pulse_agent.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "pulse_agent.utils.before_app_uninstall"
# after_app_uninstall = "pulse_agent.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "pulse_agent.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "pulse_agent.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"pulse_agent.tasks.all"
# 	],
# 	"daily": [
# 		"pulse_agent.tasks.daily"
# 	],
# 	"hourly": [
# 		"pulse_agent.tasks.hourly"
# 	],
# 	"weekly": [
# 		"pulse_agent.tasks.weekly"
# 	],
# 	"monthly": [
# 		"pulse_agent.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "pulse_agent.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "pulse_agent.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "pulse_agent.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "pulse_agent.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["pulse_agent.utils.before_request"]
# after_request = ["pulse_agent.utils.after_request"]

# Job Events
# ----------
# before_job = ["pulse_agent.utils.before_job"]
# after_job = ["pulse_agent.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"pulse_agent.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

