procedure Body;
var D: Variant;
begin
  D := Client.OpenDocument('SCH', '%OUT%\board\sheet.SchDoc');
  Client.ShowDocument(D);
  ResetParameters; AddStringParameter('Action', 'Document'); RunProcess('Sch:Zoom');
  L('shown');
end;
