{% macro icon(name, alt=None, caller=None) -%}
<svg class="icon icon-{{ name }}" aria-hidden="true"><use xlink:href="#icon-{{ name }}"></use></svg>{% if alt %}<span class="a11y-text">{{ alt }}</span>{% endif -%}
{%- endmacro %}

{% macro closed_marker(caller=None) %}
<li>{% call icon("closed") %}{% endcall %} Requires invitation or password</li>
{% endmacro %}
{% macro nonanon_marker(caller=None) %}
<li title="Other occupants see your Jabber/XMPP address." class="with-tooltip">{% call icon("nonanon") %}{% endcall %} Non-pseudonymous</li>
{% endmacro %}

{% macro dummy_avatar(address, caller=None) %}
{% set text = caller() %}
<span class="dummy-avatar" style="background-color: rgba({{ address | ccg_rgb_triplet }}, 1.0);"><span data-avatar-content="{{ text[0] }}"/></span>
{% endmacro %}

{% macro avatar(has_avatar, address, caller=None) %}
{% if has_avatar %}
<span class="real-avatar" style="background-image: url('{{ url_for('avatar_v1', address=address) }}'); "></span>
{% else %}
{% set content = caller() %}
{% call dummy_avatar(address) %}{{ content }}{% endcall %}
{% endif %}
{% endmacro %}

{% macro room_address(address, keywords=[], caller=None) -%}
{%- if address.localpart -%}
<span class="address"><span class="localpart">{{ address.localpart | jid_unescape | process_text(keywords) }}</span><span class="at">@</span><span class="domain">{{ address.domain | process_text(keywords) }}</span></span>
{%- else -%}
{{ address | process_text(keywords) }}
{%- endif -%}
{%- endmacro %}

{% macro logs_url(url, caller=None) -%}
<li><a href="{{ url }}" rel="nofollow noopener">{% call icon("history") %}{% endcall %} View history<span class="a11y-text"> of {{ caller() }} in your browser</span></a></li>
{%- endmacro %}

{% macro join_url(url, caller=None) -%}
<li><a href="{{ url }}" rel="nofollow noopener">{% call icon("join") %}{% endcall %} Join <span class="a11y-text">{{ caller() }} </span>using browser</a></li>
{%- endmacro%}

{% macro clipboard_button(caller=None) -%}
{%- set text = caller() -%}
<a title="Copy &quot;{{ text }}&quot; to clipboard" aria-label="Copy &quot;{{ text }}&quot; to clipboard" class="copy-to-clipboard" onclick="copy_to_clipboard(this); return false;" data-cliptext="{{ text }}" href="#">{% call icon("copy") %}{% endcall %}</a>
{%- endmacro %}

{% macro copyable_thing() %}
{% set text = caller() %}
<em>{{ text }}{% call clipboard_button() %}{{ text }}{% endcall %}</em>
{% endmacro %}

{% macro room_table(items, keywords=[], caller=None) %}
<ol class="roomlist">{% for address, nusers, is_open, anonymity_mode, db_name, descr, db_language, web_chat_url, http_logs_url, has_avatar in items %}
	{% set nusers = nusers | round if nusers is not none else None %}
	{% if db_name != address.localpart and db_name %}
	{% set name = db_name %}
	{% else %}
	{% set name = (address.localpart | jid_unescape) or (address | string) %}
	{% endif %}
	{% set show_descr = descr and descr != address.localpart %}
	{% set show_lang = db_language | prettify_lang(fallback=False) %}
	{% set is_nonanon = not anonymity_mode or anonymity_mode.value == "none" %}
	{% set is_closed = not is_open %}
	{% set set_lang_attr = show_lang and db_language %}
	<li class="roomcard">
		{# this is aria-hidden; we put the number of online users inline with the main content of the room card as a11y text #}
		<div class="avatar-column" aria-hidden="true">
			<div class="avatar">{% call avatar(has_avatar, address) %}{{ name }}{% endcall %}</div>
			<div class="expand"></div>
			<div class="nusers" title="Number of users online">{% call icon("users") %}{% endcall %}<span class="expand"></span><span data-content="{{ (nusers | pretty_number_info)['short'] }}"></span></div>
			<div class="expand"></div>
		</div>
		<div class="main">
			<h3 class="title"{% if set_lang_attr %} lang="{{ db_language }}"{% endif %}><a href="xmpp:{{ address }}?join" rel="nofollow">{{ name | process_text(keywords) }}</a></h3>
			<div class="addr"><a href="xmpp:{{ address }}?join" rel="nofollow">{% call room_address(address, keywords=keywords) %}{% endcall %}</a>{%- call clipboard_button() %}{{ address }}{% endcall -%}</div>
			<p class="a11y-text">{{ (nusers | pretty_number_info)['accessible'] }} users online</p>
			{% if show_descr -%}
			<p class="descr"{% if set_lang_attr %} lang="{{ db_language }}"{% endif %}>{{ descr | process_text(keywords, config["DESCRIPTION_LINKS"] | default(False)) }}</p>
			{%- endif %}
			{%- if show_lang or is_nonanon or is_closed -%}
			<div><ul class="inline slim">
			{%- if show_lang -%}<li title="Primary room language" class="with-tooltip">{% call icon("lang1", alt="Primary language:") %}{% endcall %} {{ db_language | prettify_lang }}</li>{%- endif -%}
			{%- if is_nonanon -%}{% call nonanon_marker() %}{% endcall %}{%- endif -%}
			{%- if is_closed -%}{% call closed_marker() %}{% endcall %}{%- endif -%}
			</ul></div>
			{%- endif -%}
			{%- if web_chat_url or http_logs_url -%}
			<div><ul class="actions">
			{%- if web_chat_url %}{% call join_url(web_chat_url) %}{{ name }}{% endcall %}{% endif -%}
			{%- if http_logs_url %}{% call logs_url(http_logs_url) %}{{ name }}{% endcall %}{% endif -%}</ul></div>
			{%- endif -%}
		</div>
	</li>
{% endfor %}</ol>
{% endmacro %}
