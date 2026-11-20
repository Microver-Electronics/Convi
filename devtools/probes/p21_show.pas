procedure Body;
var D: Variant;
begin
  D := Client.OpenDocument('SCH', '%ROOT%\devtools\_probe_run\XX');
  Client.ShowDocument(D);
  ResetParameters; AddStringParameter('Action', 'Document'); RunProcess('Sch:Zoom');
  L('ok');
end;
