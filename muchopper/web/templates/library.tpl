{% macro closed_marker() %}
{{ '  ' }}<span class="closed-marker" title="This room requires a password or invitation.">🔒</span>
{% endmacro %}

{% macro room_label(muc, public_info, keywords=[]) -%}
{%- if public_info.name and public_info.description and public_info.name != public_info.description and public_info.name != muc.address.localpart -%}
<span class="name">{{ public_info.name | highlight(keywords) }}</span> ({{ muc.address | highlight(keywords) }})
{%- else -%}
{%- if muc.address.localpart -%}
<span class="localpart">{{ muc.address.localpart | highlight(keywords) }}</span><span class="at">@</span><span class="domain">{{ muc.address.domain | highlight(keywords) }}</span>
{%- else -%}
{{ muc.address | highlight(keywords) }}
{%- endif -%}
{%- endif -%}
{%- endmacro %}

{% macro room_table(items, keywords=[]) %}
<table>
    <colgroup>
        <col class="label"/>
        <col class="nusers"/>
    </colgroup>
    <thead>
        <tr>
            <th>Address &amp; Description</th>
            <th>Online users</th>
        </tr>
    </thead>
    <tbody>
        {% for muc, public_info in items %}
        <tr>
            <td class="addr-descr">
                <div class="addr"><a href="xmpp:{{ muc.address }}?join">{{ room_label(muc, public_info, keywords) }}</a>{% if not muc.is_open %}{{ closed_marker() }}{% endif %}</div>
                {% set descr = public_info.description or public_info.name or public_info.subject %}
                {% if descr and descr != muc.address.localpart %}
                <div class="descr">{{ descr | highlight(keywords) }}</div>
                {% endif %}
            </td>
            <td class="nusers numeric">{{ "%.0f" | format((muc.nusers_moving_average or muc.nusers) | round) }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endmacro %}
