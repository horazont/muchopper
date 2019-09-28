{% macro icon(name, caller=None) -%}
<svg class="icon icon-{{ name }}"><use xlink:href="#icon-{{ name }}"></use></svg>
{%- endmacro %}

{% macro closed_marker() %}
<li>{% call icon("closed") %}{% endcall %} Requires invitation or password</li>
{% endmacro %}
{% macro nonanon_marker() %}
<li title="Other occupants see your Jabber/XMPP address." class="with-tooltip">{% call icon("nonanon") %}{% endcall %} Non-pseudonymous</li>
{% endmacro %}

{% macro dummy_avatar(address, caller=None) %}
{% set text = caller() %}
<div class="dummy-avatar" style="background-color: rgba({{ address | ccg_rgb_triplet }}, 1.0);"><span data-avatar-content="{{ text[0] }}"/></div>
{% endmacro %}

{% macro avatar(has_avatar, address, caller=None) %}
{% if has_avatar %}
<div class="real-avatar" style="background-image: url('{{ url_for('avatar_v1', address=address) }}'); "></div>
{% else %}
{% set content = caller() %}
{% call dummy_avatar(address) %}{{ content }}{% endcall %}
{% endif %}
{% endmacro %}

{% macro room_name(muc, public_info, caller=None) -%}
{%- if public_info.name and public_info.name != muc.address.localpart %}{{ public_info.name }}{% else %}{{ muc.address }}{% endif -%}
{%- endmacro %}

{% macro room_label(muc, public_info, keywords=[]) -%}
{%- if public_info.name and public_info.description and public_info.name != public_info.description and public_info.name != muc.address.localpart -%}
<span class="name">{{ public_info.name | highlight(keywords) }}</span><span class="address-suffix"> ({{ muc.address | highlight(keywords) }})</span>
{%- else -%}
{%- if muc.address.localpart -%}
<span class="address"><span class="localpart">{{ muc.address.localpart | highlight(keywords) }}</span><span class="at">@</span><span class="domain">{{ muc.address.domain | highlight(keywords) }}</span></span>
{%- else -%}
{{ muc.address | highlight(keywords) }}
{%- endif -%}
{%- endif -%}
{%- endmacro %}

{% macro logs_url(url, caller=None) -%}
<li><a href="{{ url }}" rel="nofollow">{% call icon("history") %}{% endcall %} View logs<span class="a11y-text"> of {{ caller() }} in your browser</span></a></li>
{%- endmacro %}

{% macro join_url(url, caller=None) -%}
<li><a href="{{ url }}" rel="nofollow">{% call icon("join") %}{% endcall %} Join <span class="a11y-text">{{ caller() }} </span>using browser</a></li>
{%- endmacro%}

{% macro clipboard_button(caller=None) %}
{% set text = caller() %}
<a title="Copy &quot;{{ text }}&quot; to clipboard" class="copy-to-clipboard" onclick="copy_to_clipboard(this); return false;" data-cliptext="{{ text }}" href="#">{% call icon("copy") %}{% endcall %}</a>
{% endmacro %}

{% macro copyable_thing() %}
{% set text = caller() %}
<em>{{ text }}{% call clipboard_button() %}{{ text }}{% endcall %}</em>
{% endmacro %}

{% macro room_table(items, keywords=[], caller=None) %}
<ol class="roomlist">
	{% for muc, public_info, has_avatar in items %}
	{% set nusers = (muc.nusers_moving_average or muc.nusers) | round %}
	{% set descr = public_info.description or public_info.name or public_info.subject %}
	{% set show_descr = descr and descr != muc.address.localpart %}
	{% set show_lang = public_info.language %}
	{% set is_nonanon = not muc.anonymity_mode or muc.anonymity_mode.value == "none" %}
	{% set is_closed = not muc.is_open %}
	{% set web_chat_url = public_info.web_chat_url %}
	{% set http_logs_url = public_info.http_logs_url %}
	<li class="roomcard">
		<div class="avatar-column">
			<div class="avatar" aria-hidden="true">{%- call avatar(has_avatar, muc.address) %}{% call
 room_name(muc, public_info) %}{% endcall %}{% endcall -%}</div>
			<div class="expand"></div>
			<div class="nusers" title="Number of users online">{% call icon("users") %}{% endcall %}{{ nusers | prettify_number }}<span class="a11y-text">&nbsp;user{{ 's' if nusers != 1 else '' }} online</span></div>
			<div class="expand"></div>
		</div>
		<div class="main">
			<div class="addr">{#- -#}
				<a href="xmpp:{{ muc.address }}?join">{{ room_label(muc, public_info, keywords) }}</a>
				{%- call clipboard_button() %}{{ muc.address }}{% endcall -%}
			</div>
			{%- if show_descr -%}
			<div class="descr">
				<span class="descr">{{ descr | highlight(keywords) }}</span>
			</div>
			{%- endif -%}
			{%- if show_lang or is_nonanon or is_closed -%}
			<div><ul class="inline slim">
			{%- if show_lang %}
			<li>{% call icon("lang1") %}{% endcall %} Primary language: {{ public_info.language | prettify_lang }}</li>
			{%- endif -%}
			{%- if is_nonanon %}{{ nonanon_marker() }}{% endif -%}
			{%- if is_closed %}{{ closed_marker() }}{% endif -%}
			</ul></div>
			{%- endif -%}
			{% if web_chat_url or http_logs_url %}
			<div><ul class="inline slim">
			{%- if web_chat_url %}{% call join_url(web_chat_url) %}{% call room_name(muc, public_info) %}{% endcall %}{% endcall %}{% endif -%}
			{%- if http_logs_url %}{% call logs_url(http_logs_url) %}{% call room_name(muc, public_info) %}{% endcall %}{% endcall %}{% endif -%}
			</ul></div>
			{% endif %}
		</div>
	</li>
	{% endfor %}
</ol>
{% endmacro %}
